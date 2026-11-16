from acidmom import db, site
from test_dedup import add, mention


def test_build_page_embeds_recent_incidents(tmp_path):
    conn = db.connect(":memory:")
    add(conn, mention(event_date="2099-01-01", lat=-23.9, lon=-46.3, coords_approximate=True,
                      summary_pt="Texto com </script> no meio."), 1)
    add(conn, mention(event_date="2000-01-01", vessels=[{"name": "Velho", "imo": "", "type": "", "flag": ""}]), 2)
    page = site.build_page(conn, tmp_path).read_text(encoding="utf-8")
    assert "Star Alpha" in page and "Velho" not in page
    assert "1 acidente(s)" in page
    assert '"approx": true' in page
    assert "Acidentes por região" in page and 'data-region="Atlântico Sul"' in page  # Santos
    assert "</script> no meio" not in page



def test_map_labels_initial_report_as_historical_after_casualty_update():
    conn = db.connect(":memory:")
    incident, _ = add(conn, mention(event_date="2099-01-01", missing=12,
                                    summary_pt="12 desaparecidos."), 1)
    mention_id = conn.execute("SELECT mention_id FROM incident_mention").fetchone()[0]
    conn.execute("UPDATE incident SET missing=0, rescued=12 WHERE id=?", (incident,))
    conn.execute("""INSERT INTO casualty_revision
                 (incident_id, mention_id, previous_data, current_data, evidence, effective_at, time_basis)
                 VALUES (?,?,?,?,?,?,?)""",
                 (incident, mention_id, "{}", "{}", "Todos resgatados.", "2099-01-01T12:00:00Z", "observed_status"))
    card, = site.incidents_for_map(conn)
    assert "Relato inicial (histórico): 12 desaparecidos." in card["summary"]
    assert "12 resgatado(s)" in card["victims"]
    assert "12 desaparecido(s)" not in card["victims"]
