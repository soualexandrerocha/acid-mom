from acidmom import db, dedup


def mention(**over):
    base = {
        "is_relevant": True, "reason": "", "vessels": [{"name": "MV Star Alpha", "imo": "", "type": "bulk carrier", "flag": ""}],
        "event_date": "2026-10-25", "event_type": "grounding", "severity": "serious", "location": "near Santos",
        "country": "Brazil", "lat": None, "lon": None, "dead": None, "missing": None, "injured": None,
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
