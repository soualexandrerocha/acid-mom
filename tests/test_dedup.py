from acidmom import db, dedup


def mention(**over):
    base = {
        "is_relevant": True, "reason": "", "vessels": [{"name": "MV Star Alpha", "imo": "", "type": "bulk carrier", "flag": ""}],
        "event_date": "2026-10-25", "event_type": "grounding", "severity": "serious", "location": "near Santos",
        "country": "Brazil", "lat": None, "lon": None, "coords_approximate": False, "dead": None, "missing": None, "injured": None,
        "pollution": None, "summary_pt": "Graneleiro encalhou perto de Santos.", "evidence": "",
    }
    return base | over


def add(conn, data, n):
    db.save_items(conn, [db.SourceItem("t", f"https://x/{n}", "t")])
    item_id = conn.execute("SELECT id FROM source_item WHERE url = ?", (f"https://x/{n}",)).fetchone()[0]
    return dedup.attach(conn, db.save_mention(conn, item_id, data), data)


def test_normalize_name():
    assert dedup.normalize_name("M/V  Star-Alpha") == "star alpha"
    assert dedup.normalize_name("Navio São João") == "sao joao"


def test_same_ship_same_days_is_merged_and_keeps_worst_numbers():
    conn = db.connect(":memory:")
    first, new1 = add(conn, mention(), 1)
    second, new2 = add(conn, mention(vessels=[{"name": "Star Alpha", "imo": "9123456", "type": "", "flag": ""}],
                                     event_date="2026-10-26", severity="very_serious", dead=2), 2)
    assert (new1, new2) == (True, False) and first == second
    row = conn.execute("SELECT * FROM incident").fetchone()
    assert (row["imo"], row["severity"], row["dead"]) == ("9123456", "very_serious", 2)
    assert conn.execute("SELECT COUNT(*) FROM incident_mention").fetchone()[0] == 2


def test_different_dates_or_ships_stay_separate():
    conn = db.connect(":memory:")
    add(conn, mention(), 1)
    _, far_in_time = add(conn, mention(event_date="2026-10-10"), 2)
    _, other_ship = add(conn, mention(vessels=[{"name": "Ocean Pearl", "imo": "", "type": "", "flag": ""}]), 3)
    assert far_in_time and other_ship


def test_far_apart_positions_do_not_match():
    conn = db.connect(":memory:")
    add(conn, mention(lat=-24.0, lon=-46.3), 1)
    _, is_new = add(conn, mention(lat=40.0, lon=-70.0), 2)
    assert is_new


def test_approximate_position_does_not_block_match_and_exact_replaces_it():
    conn = db.connect(":memory:")
    first, _ = add(conn, mention(lat=-23.9, lon=-46.3, coords_approximate=True), 1)
    second, is_new = add(conn, mention(lat=-20.0, lon=-40.0), 2)
    assert first == second and not is_new
    row = conn.execute("SELECT lat, lon, coords_approx FROM incident").fetchone()
    assert (row["lat"], row["lon"], row["coords_approx"]) == (-20.0, -40.0, 0)


def test_old_database_gets_coords_approx_column(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE incident (id INTEGER PRIMARY KEY, lat REAL)")
    old.commit()
    old.close()
    conn = db.connect(str(path))
    assert "coords_approx" in {r["name"] for r in conn.execute("PRAGMA table_info(incident)")}


def test_unnamed_ships_same_event_same_place_are_merged():
    conn = db.connect(":memory:")
    unnamed = [{"name": "", "imo": "", "type": "tanker", "flag": ""}]
    first, _ = add(conn, mention(vessels=unnamed, event_type="fire_explosion", lat=43.6, lon=39.7, coords_approximate=True), 1)
    named, is_new = add(conn, mention(vessels=[{"name": "Rio", "imo": "", "type": "tanker", "flag": ""}],
                                      event_type="fire_explosion", lat=43.58, lon=39.72, coords_approximate=True), 2)
    assert first == named and not is_new
    assert conn.execute("SELECT vessel_name FROM incident").fetchone()[0] == "Rio"
    _, other_event = add(conn, mention(vessels=unnamed, event_type="grounding", lat=43.6, lon=39.7), 3)
    _, far = add(conn, mention(vessels=unnamed, event_type="fire_explosion", lat=26.6, lon=56.3), 4)
    assert other_event and far
