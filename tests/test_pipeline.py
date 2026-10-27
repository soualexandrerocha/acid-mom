import json
from datetime import datetime, timezone
from types import SimpleNamespace

from acidmom import cli, db, extract, notify
from test_dedup import mention


class FakeClient:
    def __init__(self, answer):
        self.calls = 0
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self.answer = answer

    def _create(self, **kwargs):
        self.calls += 1
        assert kwargs["output_config"]["format"]["schema"] is extract.SCHEMA
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=json.dumps(self.answer))])


def test_too_old_items():
    assert cli.is_too_old("2022-03-13T00:00:00+00:00")
    assert not cli.is_too_old(None)
    assert not cli.is_too_old(datetime.now(timezone.utc).isoformat())


def test_keyword_filter():
    assert extract.passes_keyword_filter("Bulk carrier runs aground", "")
    assert extract.passes_keyword_filter("Navio encalha no Rio Grande", "")
    assert not extract.passes_keyword_filter("Freight rates rise", "Market report")
    assert not extract.passes_keyword_filter("Live firing exercise", "")


def test_process_waits_for_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    conn = db.connect(":memory:")
    db.save_items(conn, [db.SourceItem("B", "https://b/1", "Bulk carrier aground")])
    cli.cmd_process(conn, limit=10)
    assert conn.execute("SELECT status FROM source_item").fetchone()[0] == "new"


def test_process_skips_filtered_items_and_builds_incidents(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "teste")
    conn = db.connect(":memory:")
    db.save_items(conn, [db.SourceItem("A", "https://a/1", "Freight rates rise"),
                         db.SourceItem("B", "https://b/1", "Bulk carrier Star Alpha aground", "near Santos")])
    today = datetime.now(timezone.utc).date().isoformat()
    fake = FakeClient(mention(severity="very_serious", dead=1, event_date=today))
    monkeypatch.setattr(cli.anthropic, "Anthropic", lambda: fake)
    cli.cmd_process(conn, limit=10)
    statuses = dict(conn.execute("SELECT url, status FROM source_item").fetchall())
    assert statuses == {"https://a/1": "filtered_out", "https://b/1": "extracted"}
    assert fake.calls == 1

    sent = []
    monkeypatch.setattr(notify, "send_email", lambda s, t, h: sent.append((s, t)) or True)
    assert notify.send_alerts(conn) == 1
    assert "muito grave" in sent[0][0] and "https://b/1" in sent[0][1]
    assert notify.send_alerts(conn) == 0  # não repete o aviso


def test_irrelevant_items_create_nothing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "teste")
    conn = db.connect(":memory:")
    db.save_items(conn, [db.SourceItem("B", "https://b/2", "Fishing boat capsized")])
    monkeypatch.setattr(cli.anthropic, "Anthropic", lambda: FakeClient(mention(is_relevant=False)))
    cli.cmd_process(conn, limit=10)
    assert conn.execute("SELECT status FROM source_item").fetchone()[0] == "not_relevant"
    assert conn.execute("SELECT COUNT(*) FROM incident").fetchone()[0] == 0


def test_weekly_report_lists_incidents():
    conn = db.connect(":memory:")
    from test_dedup import add
    add(conn, mention(event_date=datetime.now(timezone.utc).date().isoformat()), 1)
    add(conn, mention(event_date="2022-03-13", vessels=[{"name": "Advantage Spring", "imo": "", "type": "", "flag": ""}]), 2)
    subject, text, html_body = notify.weekly_report(conn)
    assert "1 acidente(s)" in subject and "Star Alpha" in text and "encalhe" in html_body
    assert "Advantage Spring" not in text


def test_attack_titles_never_reach_claude(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "teste")
    conn = db.connect(":memory:")
    db.save_items(conn, [db.SourceItem("G", "https://g/1", "Iran says cargo ship sank after hitting a mine in Strait of Hormuz"),
                         db.SourceItem("G", "https://g/2", "Incêndio em petroleiro atingido por míssil")])
    fake = FakeClient(mention())
    monkeypatch.setattr(cli.anthropic, "Anthropic", lambda: fake)
    cli.cmd_process(conn, limit=10)
    assert {r[0] for r in conn.execute("SELECT status FROM source_item")} == {"attack"}
    assert fake.calls == 0
    assert not extract.is_attack("Bulk carrier runs aground off Santos")


def test_no_immediate_alert_for_old_accidents(monkeypatch):
    conn = db.connect(":memory:")
    from test_dedup import add
    add(conn, mention(severity="very_serious", event_date="2026-01-10"), 1)
    sent = []
    monkeypatch.setattr(notify, "send_email", lambda s, t, h: sent.append(s) or True)
    assert notify.send_alerts(conn) == 0 and not sent


def test_uncertain_cause_gets_no_alert_but_is_marked(monkeypatch):
    conn = db.connect(":memory:")
    from test_dedup import add
    today = datetime.now(timezone.utc).date().isoformat()
    add(conn, mention(severity="very_serious", event_date=today, cause_uncertain=True), 1)
    monkeypatch.setattr(notify, "send_email", lambda s, t, h: True)
    assert notify.send_alerts(conn) == 0
    row = conn.execute("SELECT * FROM incident").fetchone()
    assert "pode ter sido ataque" in notify.format_incident(conn, row)[0]
    # Uma fonte que explica a causa tira a dúvida.
    add(conn, mention(severity="very_serious", event_date=today, cause_uncertain=False), 2)
    assert conn.execute("SELECT cause_uncertain FROM incident").fetchone()[0] == 0
