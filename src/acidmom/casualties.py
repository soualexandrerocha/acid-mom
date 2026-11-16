"""Conservative casualty updates; original mentions and revision evidence remain immutable."""
import json
from datetime import datetime, timezone

FIELDS = ("dead", "missing", "injured", "rescued")


def _time(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        return None


def apply_casualty_update(conn, incident_id, mention_id, mention, *, is_new=False):
    row = conn.execute("SELECT * FROM incident WHERE id=?", (incident_id,)).fetchone()
    source = conn.execute("SELECT s.* FROM source_item s JOIN mention m ON m.source_item_id=s.id WHERE m.id=?", (mention_id,)).fetchone()
    before = {k: row[k] for k in FIELDS}
    after = dict(before)
    published = _time(source["published_at"])
    effective = _time(mention.get("casualty_as_of"))
    quote = mention.get("casualty_evidence") or ""
    evidence_ok = bool(quote.strip()) and quote in (source["title"] + "\n" + source["body"])
    time_basis = "observed_status"
    if (effective is None and mention.get("casualty_all_accounted_for") is True
            and mention.get("missing") == 0 and evidence_ok):
        effective = published
        time_basis = "publication_assertion"
    previous = conn.execute("""SELECT s.published_at, m.data FROM incident_mention im
        JOIN mention m ON m.id=im.mention_id JOIN source_item s ON s.id=m.source_item_id
        WHERE im.incident_id=? AND im.mention_id!=?""", (incident_id, mention_id)).fetchall()
    # Only casualty-bearing evidence establishes chronology, not unrelated articles.
    previous_data = [(r[0], json.loads(r[1])) for r in previous]
    eligible = (mention.get("casualty_status") == "confirmed_update" and evidence_ok
                and published is not None and effective is not None and effective <= published)
    latest = conn.execute("SELECT effective_at FROM casualty_revision WHERE incident_id=? ORDER BY id DESC LIMIT 1", (incident_id,)).fetchone()
    protected = latest is not None
    confirmed_change = False
    confirmed_status = False
    for key in FIELDS:
        value = mention.get(key)
        if type(value) is not int or value < 0:
            continue
        old = before[key]
        previous_times = [_time(d.get("casualty_as_of")) or _time(t)
                          for t, d in previous_data if d.get(key) is not None]
        # After a verified revision, its effective time is authoritative; later recaps
        # that were deliberately rejected must not block a genuinely newer update.
        if protected:
            previous_times = [_time(latest["effective_at"])]
        ordered = bool(previous_times) and all(t is not None and t < effective for t in previous_times) if eligible else False
        confirmed = eligible and ordered
        if time_basis == "publication_assertion" and key in ("dead", "injured") and old is not None and value < old:
            confirmed = False
        confirmed_status = confirmed_status or confirmed
        if is_new or confirmed or (not protected and (old is None or value > old)):
            after[key] = value
            confirmed_change = confirmed_change or (confirmed and value != old)
    conn.execute("UPDATE incident SET dead=?,missing=?,injured=?,rescued=? WHERE id=?", (*after.values(), incident_id))
    if not is_new and confirmed_status:
        conn.execute("""INSERT OR IGNORE INTO casualty_revision
            (incident_id,mention_id,previous_data,current_data,evidence,effective_at,time_basis,pending_alert)
            VALUES (?,?,?,?,?,?,?,?)""", (incident_id, mention_id, json.dumps(before), json.dumps(after),
            quote, effective.isoformat(), time_basis, int(confirmed_change and row["alert_sent_at"] is not None)))
