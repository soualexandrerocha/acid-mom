from acidmom import db, dedup
from test_dedup import mention, add


def ship(name):
    return {"name": name, "imo": "", "type": ""}


def collision(**kwargs):
    return mention(event_type="collision", event_date="2026-11-13", lat=29.93,
                   lon=-90.06, **kwargs)


def judged_add(conn, data, n, judge):
    db.save_items(conn, [db.SourceItem("t", f"https://j/{n}", "t")])
    item = conn.execute("SELECT id FROM source_item WHERE url=?", (f"https://j/{n}",)).fetchone()[0]
    return dedup.attach(conn, db.save_mention(conn, item, data), data, judge=judge)


def test_second_participant_is_recognized_from_linked_evidence():
    conn = db.connect(":memory:")
    first, _ = add(conn, collision(vessels=[ship("Atlantic Asphalt"), ship("American Heritage")]), 1)
    same, new = add(conn, collision(vessels=[ship("American Heritage")]), 2)
    assert same == first and not new


def test_disjoint_collision_names_reach_review_without_forced_merge():
    conn = db.connect(":memory:")
    first, _ = add(conn, collision(vessels=[ship("Atlantic Asphalt")]), 1)
    other = collision(vessels=[ship("American Heritage")])
    assert dedup.find_match(conn, other) is None
    candidates = dedup.judge_candidates(conn, other)
    assert [c["id"] for c in candidates] == [first]
    assert candidates[0]["reports"][0]["vessels"][0]["name"] == "Atlantic Asphalt"
    _, new = judged_add(conn, other, 2, lambda m, c: None)
    assert new


def test_different_named_noncollision_and_distant_collision_not_candidates():
    conn = db.connect(":memory:")
    add(conn, collision(vessels=[ship("Atlantic Asphalt")]), 1)
    assert not dedup.judge_candidates(conn, collision(vessels=[ship("American Heritage")]) | {"lat": 40})
    conn = db.connect(":memory:")
    add(conn, mention(lat=29.93, lon=-90.06), 1)
    assert not dedup.judge_candidates(conn, mention(vessels=[ship("Other vessel")], lat=29.93, lon=-90.06))


def test_long_gap_unnamed_can_be_reviewed_but_not_auto_matched():
    conn = db.connect(":memory:")
    first, _ = add(conn, collision(vessels=[]) | {"event_date": "2026-11-08"}, 1)
    later = collision(vessels=[])
    assert dedup.find_match(conn, later) is None
    assert [c["id"] for c in dedup.judge_candidates(conn, later)] == [first]
    assert not dedup.judge_candidates(conn, later | {"lat": None, "lon": None, "location": "unknown"})


def test_explicit_event_date_replaces_only_documented_inference():
    for basis, expected in [("inferred", "2026-11-13"), ("publication", "2026-11-13"),
                            ("unknown", "2026-11-08"), ("explicit", "2026-11-08"), (None, "2026-11-08")]:
        conn = db.connect(":memory:")
        first, _ = add(conn, mention(event_date="2026-11-08", event_date_basis=basis), 1)
        same, new = add(conn, mention(event_date="2026-11-13", event_date_basis="explicit",
                                     event_date_evidence="The accident occurred on November 13."), 2)
        assert first == same and not new
        assert conn.execute("SELECT event_date FROM incident").fetchone()[0] == expected
        assert conn.execute("SELECT count(*) FROM incident_mention").fetchone()[0] == 2


def test_later_date_without_evidence_never_wins_and_distance_gate_applies():
    conn = db.connect(":memory:")
    add(conn, mention(event_date="2026-11-08", event_date_basis="inferred", lat=0, lon=0), 1)
    add(conn, mention(event_date="2026-11-13", event_date_basis="explicit", lat=0, lon=0), 2)
    assert conn.execute("SELECT event_date FROM incident").fetchone()[0] == "2026-11-08"
    _, new = add(conn, mention(event_date="2026-11-13", lat=40, lon=40), 3)
    assert new


def test_second_participant_does_not_replace_primary_identity():
    conn = db.connect(":memory:")
    first, _ = add(conn, collision(vessels=[ship("Atlantic Asphalt")]), 1)
    second = collision(vessels=[ship("American Heritage") | {"imo": "1234567"}])
    same, new = judged_add(conn, second, 2, lambda m, c: c[0]["id"])
    assert same == first and not new
    row = conn.execute("SELECT * FROM incident").fetchone()
    assert row["vessel_name"] == "Atlantic Asphalt" and row["imo"] is None
    again, new = add(conn, second, 3)
    assert again == first and not new


def test_historical_anonymous_report_is_not_an_identity_wildcard():
    conn = db.connect(":memory:")
    add(conn, collision(vessels=[]), 1)
    add(conn, collision(vessels=[ship("Atlantic Asphalt")]), 2)
    assert dedup.find_match(conn, collision(vessels=[ship("American Heritage")])) is None



def test_same_day_collision_with_sparse_location_can_reach_review_only():
    conn = db.connect(":memory:")
    precise = collision(vessels=[ship("Atlantic Asphalt")], country="United States",
                        location="Mississippi River, New Orleans", injured=32)
    first, _ = add(conn, precise, 1)
    broad = collision(vessels=[ship("American Heritage")], country="United States",
                      location="United States", injured=32) | {"lat": None, "lon": None}
    assert dedup.find_match(conn, broad) is None
    assert [c["id"] for c in dedup.judge_candidates(conn, broad)] == [first]
    assert not dedup.judge_candidates(conn, broad | {"injured": 31})
    assert not dedup.judge_candidates(conn, broad | {"country": "Canada"})
