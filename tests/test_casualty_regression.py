import json
import pytest
from acidmom import db, dedup, notify


def add(conn, n, published, **changes):
    data = dict(vessels=[dict(name="BETUL 8")], event_date="2026-11-10", event_type="flooding_foundering",
                severity="very_serious", summary_pt="Doze pessoas desaparecidas.", missing=12)
    data.update(changes)
    quote = data.get("casualty_evidence", "")
    db.save_items(conn, [db.SourceItem("test", f"https://example.invalid/{n}", "BETUL 8", quote, published)])
    mid = db.save_mention(conn, conn.execute("SELECT MAX(id) FROM source_item").fetchone()[0], data)
    return dedup.attach(conn, mid, data)[0]


def setup():
    conn = db.connect(":memory:")
    iid = add(conn, 1, "2026-11-10T10:00:00Z")
    conn.execute("UPDATE incident SET alert_sent_at='2026-11-10T11:00:00Z' WHERE id=?", (iid,))
    return conn


def correction(**changes):
    return dict(missing=0, rescued=12, casualty_status="confirmed_update",
                casualty_as_of="2026-11-11T11:00:00Z",
                casualty_evidence="All twelve missing crew were rescued; none remain missing.") | changes


def test_confirmed_resolution_preserves_history_and_sends_once(monkeypatch):
    conn = setup()
    add(conn, 2, "2026-11-11T12:00:00Z", **correction())
    row = conn.execute("SELECT * FROM incident").fetchone()
    assert (row["missing"], row["rescued"]) == (0, 12)
    assert json.loads(conn.execute("SELECT data FROM mention WHERE id=1").fetchone()[0])["missing"] == 12
    text, _ = notify.format_incident(conn, row)
    assert "Relato inicial (histórico)" in text and "0 desaparecido" in text
    sent = []
    monkeypatch.setattr(notify, "send_email", lambda *args: sent.append(args) or True)
    assert notify.send_alerts(conn) == 1
    assert notify.send_alerts(conn) == 0
    assert len(sent) == 1 and "Atualização de vítimas" in sent[0][0]


@pytest.mark.parametrize("changes,published", [
    ({"missing": None}, "2026-11-11T12:00:00Z"),
    ({"casualty_status": "reported"}, "2026-11-11T12:00:00Z"),
    ({"casualty_as_of": ""}, "2026-11-11T12:00:00Z"),
    ({"casualty_as_of": "2026-11-09"}, "2026-11-11T12:00:00Z"),
    ({"casualty_as_of": "2026-11-10T10:00:00Z"}, "2026-11-11T12:00:00Z"),
    ({}, None),
])
def test_unknown_ambiguous_and_old_cannot_clear_missing(changes, published):
    conn = setup()
    add(conn, 2, published, **correction(**changes))
    assert conn.execute("SELECT missing FROM incident").fetchone()[0] == 12


def test_old_report_cannot_resurrect_resolved_missing():
    conn = setup()
    add(conn, 2, "2026-11-11T12:00:00Z", **correction())
    add(conn, 3, "2026-11-10T12:00:00Z", missing=12)
    add(conn, 4, "2026-11-12T12:00:00Z", missing=12)
    assert conn.execute("SELECT missing FROM incident").fetchone()[0] == 0


def test_correction_retry_and_no_retroactive_initial_alert(monkeypatch):
    conn = setup()
    add(conn, 2, "2026-11-11T12:00:00Z", **correction())
    monkeypatch.setattr(notify, "send_email", lambda *args: False)
    notify.send_alerts(conn)
    assert conn.execute("SELECT sent_at FROM casualty_revision").fetchone()[0] is None
    monkeypatch.setattr(notify, "send_email", lambda *args: True)
    notify.send_alerts(conn)
    assert conn.execute("SELECT sent_at FROM casualty_revision").fetchone()[0]
    conn = db.connect(":memory:")
    add(conn, 1, "2026-11-10T10:00:00Z")
    add(conn, 2, "2026-11-11T12:00:00Z", **correction())
    assert notify.send_casualty_corrections(conn) == 0


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "db.sqlite"
    db.connect(path).close()
    conn = db.connect(path)
    assert "rescued" in {r[1] for r in conn.execute("PRAGMA table_info(incident)")}
    assert conn.execute("SELECT COUNT(*) FROM casualty_revision").fetchone()[0] == 0


def test_explicit_all_accounted_for_uses_publication_assertion_time():
    conn = setup()
    add(conn, 2, "2026-11-11T12:00:00Z", **correction(casualty_as_of="", casualty_all_accounted_for=True))
    assert conn.execute("SELECT missing FROM incident").fetchone()[0] == 0
    assert conn.execute("SELECT time_basis FROM casualty_revision").fetchone()[0] == "publication_assertion"


def test_unrelated_later_article_does_not_block_status():
    conn = setup()
    add(conn, 2, "2026-11-12T12:00:00Z", missing=None)
    add(conn, 3, "2026-11-11T12:00:00Z", **correction())
    assert conn.execute("SELECT missing FROM incident").fetchone()[0] == 0


def test_unverifiable_quote_rejected():
    conn = setup()
    data = correction()
    db.save_items(conn, [db.SourceItem("test", "https://example.invalid/unverified", "Unrelated text", "", "2026-11-11T12:00:00Z")])
    mid = db.save_mention(conn, 2, data)
    from acidmom.casualties import apply_casualty_update
    apply_casualty_update(conn, 1, mid, data)
    assert conn.execute("SELECT missing FROM incident").fetchone()[0] == 12


def test_map_and_weekly_expose_history_and_current_status():
    from acidmom.site import incidents_for_map
    conn = setup()
    add(conn, 2, "2026-11-11T12:00:00Z", **correction())
    # This formatting test should not expire as the real clock advances.
    conn.execute("UPDATE incident SET event_date=date('now')")
    row = incidents_for_map(conn)[0]
    assert "histórico" in row["summary"] and "none remain missing" in row["summary"]
    assert "0 desaparecido" in row["victims"]
    _, text, _ = notify.weekly_report(conn)
    assert "histórico" in text and "0 desaparecido" in text


def test_failed_correction_exception_remains_retryable(monkeypatch):
    conn = setup()
    add(conn, 2, "2026-11-11T12:00:00Z", **correction())
    def fail(*args):
        raise OSError("mock SMTP failure")
    monkeypatch.setattr(notify, "send_email", fail)
    with pytest.raises(OSError):
        notify.send_casualty_corrections(conn)
    assert conn.execute("SELECT sent_at FROM casualty_revision").fetchone()[0] is None


def test_repeated_same_confirmed_status_does_not_queue_duplicate():
    conn = setup()
    add(conn, 2, "2026-11-11T12:00:00Z", **correction())
    add(conn, 3, "2026-11-12T12:00:00Z", **correction(casualty_as_of="2026-11-12T11:00:00Z"))
    assert conn.execute("SELECT COUNT(*) FROM casualty_revision WHERE pending_alert=1").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM casualty_revision").fetchone()[0] == 2
