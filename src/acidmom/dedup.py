"""Deduplicação: decide se uma menção nova é um acidente já conhecido ou um acidente novo."""

import math
import re
import sqlite3
import unicodedata
from datetime import date, timedelta
from difflib import SequenceMatcher

MATCH_THRESHOLD = 0.8
DATE_WINDOW_DAYS = 3
MAX_DISTANCE_KM = 300

SEVERITY_RANK = {"unknown": 0, "marine_incident": 1, "less_serious": 2, "serious": 3, "very_serious": 4}
VESSEL_PREFIXES = re.compile(r"^(mv|m/v|mt|m/t|ms|m/s|sv|lng|lpg|navio)\s+", re.IGNORECASE)


def normalize_name(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower().strip()
    name = VESSEL_PREFIXES.sub("", name)
    return re.sub(r"[^a-z0-9]+", " ", name).strip()


def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value[:10]) if value else None
    except ValueError:
        return None


def distance_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def match_score(mention: dict, incident: sqlite3.Row) -> float:
    """0 a 1: chance de a menção e o acidente serem o mesmo evento."""
    m_date, i_date = _parse_date(mention.get("event_date")), _parse_date(incident["event_date"])
    if m_date and i_date and abs((m_date - i_date).days) > DATE_WINDOW_DAYS:
        return 0.0
    # Só compara distância entre coordenadas exatas: as aproximadas podem estar longe do ponto real.
    if (None not in (mention.get("lat"), mention.get("lon"), incident["lat"], incident["lon"])
            and not mention.get("coords_approximate") and not incident["coords_approx"]
            and distance_km(mention["lat"], mention["lon"], incident["lat"], incident["lon"]) > MAX_DISTANCE_KM):
        return 0.0

    best = 0.0
    for vessel in mention.get("vessels", []):
        if vessel.get("imo") and vessel["imo"] == incident["imo"]:
            return 1.0
        if vessel.get("name") and incident["vessel_name"]:
            a, b = normalize_name(vessel["name"]), normalize_name(incident["vessel_name"])
            if a and b:
                best = max(best, SequenceMatcher(None, a, b).ratio())
    if best and mention.get("event_type") == incident["event_type"]:
        best = min(1.0, best + 0.05)
    return best


def find_match(conn: sqlite3.Connection, mention: dict) -> tuple[int, float] | None:
    """Procura um acidente compatível entre os recentes. Devolve (id, escore) ou None."""
    ref = _parse_date(mention.get("event_date")) or date.today()
    since = (ref - timedelta(days=DATE_WINDOW_DAYS + 7)).isoformat()
    rows = conn.execute(
        "SELECT * FROM incident WHERE COALESCE(event_date, created_at) >= ?", (since,)
    ).fetchall()
    scored = [(row["id"], match_score(mention, row)) for row in rows]
    scored = [s for s in scored if s[1] >= MATCH_THRESHOLD]
    return max(scored, key=lambda s: s[1]) if scored else None


def _first_vessel(mention: dict) -> dict:
    return (mention.get("vessels") or [{}])[0]


def create_incident(conn: sqlite3.Connection, mention: dict) -> int:
    vessel = _first_vessel(mention)
    cur = conn.execute(
        """INSERT INTO incident (event_date, vessel_name, imo, vessel_type, event_type, severity,
               location, country, lat, lon, coords_approx, dead, missing, injured, pollution, summary)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (mention.get("event_date") or None, vessel.get("name") or None, vessel.get("imo") or None,
         vessel.get("type") or None, mention["event_type"], mention["severity"],
         mention.get("location") or None, mention.get("country") or None, mention.get("lat"),
         mention.get("lon"), bool(mention.get("coords_approximate")), mention.get("dead"), mention.get("missing"), mention.get("injured"),
         mention.get("pollution"), mention.get("summary_pt")),
    )
    return cur.lastrowid


def merge_into(conn: sqlite3.Connection, incident_id: int, mention: dict) -> None:
    """Completa o acidente com a menção nova: preenche vazios e mantém os números mais altos."""
    row = conn.execute("SELECT * FROM incident WHERE id = ?", (incident_id,)).fetchone()
    vessel = _first_vessel(mention)

    def fill(current, new):
        return current if current not in (None, "") else (new or None)

    def highest(current, new):
        values = [v for v in (current, new) if v is not None]
        return max(values) if values else None

    severity = max(row["severity"], mention["severity"], key=lambda s: SEVERITY_RANK.get(s, 0))
    # Coordenadas exatas substituem as aproximadas; entre duas aproximadas, fica a primeira.
    lat, lon, approx = row["lat"], row["lon"], row["coords_approx"]
    if mention.get("lat") is not None and (lat is None or (approx and not mention.get("coords_approximate"))):
        lat, lon, approx = mention["lat"], mention["lon"], bool(mention.get("coords_approximate"))
    conn.execute(
        """UPDATE incident SET event_date = ?, vessel_name = ?, imo = ?, vessel_type = ?,
               severity = ?, location = ?, country = ?, lat = ?, lon = ?, coords_approx = ?,
               dead = ?, missing = ?,
               injured = ?, pollution = ?, updated_at = datetime('now')
           WHERE id = ?""",
        (fill(row["event_date"], mention.get("event_date")), fill(row["vessel_name"], vessel.get("name")),
         fill(row["imo"], vessel.get("imo")), fill(row["vessel_type"], vessel.get("type")), severity,
         fill(row["location"], mention.get("location")), fill(row["country"], mention.get("country")),
         lat, lon, approx,
         highest(row["dead"], mention.get("dead")), highest(row["missing"], mention.get("missing")),
         highest(row["injured"], mention.get("injured")),
         row["pollution"] if row["pollution"] is not None else mention.get("pollution"),
         incident_id),
    )


def attach(conn: sqlite3.Connection, mention_id: int, mention: dict) -> tuple[int, bool]:
    """Liga a menção a um acidente (existente ou novo). Devolve (incident_id, é_novo)."""
    match = find_match(conn, mention)
    if match:
        incident_id, score = match
        merge_into(conn, incident_id, mention)
        is_new = False
    else:
        incident_id, score, is_new = create_incident(conn, mention), 1.0, True
    conn.execute(
        "INSERT INTO incident_mention (incident_id, mention_id, match_score) VALUES (?, ?, ?)",
        (incident_id, mention_id, score),
    )
    return incident_id, is_new
