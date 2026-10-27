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
    assert "</script> no meio" not in page
