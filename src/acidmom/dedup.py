"""Deduplicação: decide se uma menção nova é um acidente já conhecido ou um acidente novo."""

import json
import math
import re
import sqlite3
import unicodedata
from datetime import date, timedelta
from difflib import SequenceMatcher

from .casualties import apply_casualty_update

MATCH_THRESHOLD = 0.8
DATE_WINDOW_DAYS = 3
MAX_DISTANCE_KM = 300
# Sem nome de navio, a mesma ocorrência no mesmo lugar conta como o mesmo acidente.
UNNAMED_DISTANCE_KM = 50
UNNAMED_SCORE = 0.85
# Mesmo nome (quase idêntico) aceita datas mais distantes: notícias sobre o mesmo naufrágio
# seguem saindo por dias, e a data do evento às vezes vem como a data da notícia.
SAME_NAME_RATIO = 0.95
SAME_NAME_DATE_WINDOW_DAYS = 10
# Segunda opinião do Claude: só para candidatos do mesmo tipo de evento e não tão distantes.
JUDGE_MAX_DISTANCE_KM = 1500
JUDGE_MAX_CANDIDATES = 15
JUDGE_SCORE = 0.8

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


def _name_ratio(mention: dict, incident: sqlite3.Row) -> float:
    best = 0.0
    for vessel in mention.get("vessels", []):
        if vessel.get("name") and incident["vessel_name"]:
            a, b = normalize_name(vessel["name"]), normalize_name(incident["vessel_name"])
            if a and b:
                best = max(best, SequenceMatcher(None, a, b).ratio())
    return best


def _same_vessel(mention: dict, incident: sqlite3.Row) -> bool:
    if any(v.get("imo") and v["imo"] == incident["imo"] for v in mention.get("vessels", [])):
        return True
    return _name_ratio(mention, incident) >= SAME_NAME_RATIO


def match_score(mention: dict, incident: sqlite3.Row) -> float:
    """0 a 1: chance de a menção e o acidente serem o mesmo evento."""
    m_date, i_date = _parse_date(mention.get("event_date")), _parse_date(incident["event_date"])
    gap = abs((m_date - i_date).days) if m_date and i_date else 0
    if gap > SAME_NAME_DATE_WINDOW_DAYS:
        return 0.0
    # Só compara distância entre coordenadas exatas: as aproximadas podem estar longe do ponto real.
    if (None not in (mention.get("lat"), mention.get("lon"), incident["lat"], incident["lon"])
            and not mention.get("coords_approximate") and not incident["coords_approx"]
            and distance_km(mention["lat"], mention["lon"], incident["lat"], incident["lon"]) > MAX_DISTANCE_KM):
        return 0.0

    if gap > DATE_WINDOW_DAYS:
        # Só o mesmo navio (mesmo IMO ou nome quase igual) passa da janela curta.
        return 1.0 if _same_vessel(mention, incident) else 0.0
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
    named = any(v.get("name") for v in mention.get("vessels", []))
    if (not best and (not named or not incident["vessel_name"])
            and mention.get("event_type") == incident["event_type"]
            and None not in (mention.get("lat"), mention.get("lon"), incident["lat"], incident["lon"])
            and distance_km(mention["lat"], mention["lon"], incident["lat"], incident["lon"]) <= UNNAMED_DISTANCE_KM):
        return UNNAMED_SCORE
    return best


def _linked_reports(conn, incident_id):
    rows = conn.execute(
        "SELECT m.data FROM mention m JOIN incident_mention im ON im.mention_id = m.id "
        "WHERE im.incident_id = ? ORDER BY m.id", (incident_id,)
    ).fetchall()
    return [json.loads(row["data"]) for row in rows]


def _with_history(conn, row, reports=None):
    """Keep participant identity and evidence in existing, provenance-linked mentions."""
    result = dict(row)
    reports = _linked_reports(conn, row["id"]) if reports is None else reports
    vessels = [{"name": row["vessel_name"], "imo": row["imo"], "type": row["vessel_type"]}]
    for report in reports:
        for vessel in report.get("vessels", []):
            if vessel not in vessels:
                vessels.append(vessel)
    result["vessels"] = vessels
    result["reports"] = [{k: (r.get(k)[:2000] if isinstance(r.get(k), str) else r.get(k)) for k in (
        "vessels", "event_date", "event_date_basis", "event_date_evidence",
        "location", "summary_pt", "evidence", "dead", "missing", "injured")}
        for r in reports[-5:]]
    return result


def _participant_views(row):
    vessels = [v for v in row["vessels"] if v.get("name") or v.get("imo")]
    return [dict(row) | {"vessel_name": v.get("name"), "imo": v.get("imo")}
            for v in vessels] or [dict(row)]


def _strong_place(mention, row):
    # Approximate coordinates alone are not evidence that two collisions share a berth.
    if (None not in (mention.get("lat"), mention.get("lon"), row["lat"], row["lon"])
            and not mention.get("coords_approximate") and not row["coords_approx"]):
        return distance_km(mention["lat"], mention["lon"], row["lat"], row["lon"]) <= 10
    place = normalize_name(mention.get("location") or "")
    country = normalize_name(mention.get("country") or "")
    return (len(place.split()) >= 3 and place == normalize_name(row["location"] or "")
            and bool(country) and country == normalize_name(row["country"] or ""))


def _same_day_casualty_lead(mention, row):
    """Admit review, never auto-merge, when one report only names a country."""
    day = _parse_date(mention.get("event_date"))
    country = normalize_name(mention.get("country") or "")
    if not day or day != _parse_date(row["event_date"]) or not country:
        return False
    if country != normalize_name(row["country"] or ""):
        return False
    places = [normalize_name(location or "") for location in (mention.get("location"), row["location"])]
    if not any(place in ("", country, f"{country} {country}") for place in places):
        return False
    if (None not in (mention.get("lat"), mention.get("lon"), row["lat"], row["lon"])
            and not mention.get("coords_approximate") and not row["coords_approx"]):
        return False  # Both exact placements must instead satisfy the strong-place gate.
    return any(type(mention.get(k)) is int and mention[k] >= 5 and mention[k] == row[k]
               for k in ("dead", "missing", "injured"))


def reconciled_event_date(conn, row, mention):
    """Only replace an inferred/publication date with explicitly evidenced event dating.

    Legacy unknown provenance and conflicting explicit dates remain unresolved in the
    linked reports; neither arrival order nor article publication time settles them.
    """
    old, new = row["event_date"], mention.get("event_date")
    if not old:
        return new or None
    if not _parse_date(new) or old == new:
        return old
    if mention.get("event_date_basis") != "explicit" or not mention.get("event_date_evidence"):
        return old
    supporting = [r for r in _linked_reports(conn, row["id"]) if r.get("event_date") == old]
    if supporting and all(r.get("event_date_basis") in ("inferred", "publication") for r in supporting):
        return new
    return old


def find_match(conn: sqlite3.Connection, mention: dict) -> tuple[int, float] | None:
    """Procura um acidente compatível entre os recentes. Devolve (id, escore) ou None."""
    ref = _parse_date(mention.get("event_date")) or date.today()
    since = (ref - timedelta(days=SAME_NAME_DATE_WINDOW_DAYS + 3)).isoformat()
    until = (ref + timedelta(days=SAME_NAME_DATE_WINDOW_DAYS)).isoformat()
    rows = conn.execute(
        "SELECT * FROM incident WHERE COALESCE(event_date, substr(created_at, 1, 10)) BETWEEN ? AND ?",
        (since, until),
    ).fetchall()
    # Batch historical identity lookup rather than one query per incident.
    histories = {row["id"]: [] for row in rows}
    ids = list(histories)
    for start in range(0, len(ids), 500):
        batch = ids[start:start + 500]
        for linked in conn.execute(
            "SELECT im.incident_id, m.data FROM mention m JOIN incident_mention im "
            "ON im.mention_id = m.id WHERE im.incident_id IN (" + ",".join("?" for _ in batch) + ") ORDER BY m.id",
            batch,
        ):
            histories[linked["incident_id"]].append(json.loads(linked["data"]))
    scored = [(row["id"], max(match_score(mention, view)
                              for view in _participant_views(_with_history(conn, row, histories[row["id"]]))))
              for row in rows]
    scored = [s for s in scored if s[1] >= MATCH_THRESHOLD]
    return max(scored, key=lambda s: s[1]) if scored else None


def judge_candidates(conn: sqlite3.Connection, mention: dict) -> list[dict]:
    """Acidentes que podem ser o mesmo evento, mas que as regras não conseguem decidir.

    Caso típico: notícias em vários idiomas sobre o mesmo naufrágio, sem o nome do navio
    e com lugares vagos ("Mar Mediterrâneo", "ao largo de Chipre").
    """
    ref = _parse_date(mention.get("event_date")) or date.today()
    since = (ref - timedelta(days=SAME_NAME_DATE_WINDOW_DAYS)).isoformat()
    until = (ref + timedelta(days=SAME_NAME_DATE_WINDOW_DAYS)).isoformat()
    rows = conn.execute(
        """SELECT * FROM incident WHERE event_type = ?
               AND COALESCE(event_date, substr(created_at, 1, 10)) BETWEEN ? AND ?
           ORDER BY id DESC""",
        (mention.get("event_type"), since, until),
    ).fetchall()
    named = any(v.get("name") for v in mention.get("vessels", []))
    out = []
    for row in rows:
        row = _with_history(conn, row)
        views = _participant_views(row)
        disjoint = named and row["vessel_name"] and max(_name_ratio(mention, v) for v in views) < 0.6
        # Different names can be different participants, not necessarily different collisions.
        # Place/time only admits review; it must never force an automatic merge.
        if disjoint and not (mention.get("event_type") == "collision"
                             and (_strong_place(mention, row) or _same_day_casualty_lead(mention, row))):
            continue
        old_date = _parse_date(row["event_date"])
        gap = abs((ref - old_date).days) if old_date else 0
        if gap > DATE_WINDOW_DAYS and not _strong_place(mention, row):
            continue
        if (None not in (mention.get("lat"), mention.get("lon"), row["lat"], row["lon"])
                and distance_km(mention["lat"], mention["lon"], row["lat"], row["lon"]) > JUDGE_MAX_DISTANCE_KM):
            continue
        out.append(row)
    return out[:JUDGE_MAX_CANDIDATES]


def _first_vessel(mention: dict) -> dict:
    return (mention.get("vessels") or [{}])[0]


def create_incident(conn: sqlite3.Connection, mention: dict) -> int:
    vessel = _first_vessel(mention)
    cur = conn.execute(
        """INSERT INTO incident (event_date, vessel_name, imo, vessel_type, event_type, severity,
               location, country, lat, lon, coords_approx, cause_uncertain, dead, missing, injured,
               pollution, summary)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (mention.get("event_date") or None, vessel.get("name") or None, vessel.get("imo") or None,
         vessel.get("type") or None, mention["event_type"], mention["severity"],
         mention.get("location") or None, mention.get("country") or None, mention.get("lat"),
         mention.get("lon"), bool(mention.get("coords_approximate")), bool(mention.get("cause_uncertain")),
         mention.get("dead"), mention.get("missing"), mention.get("injured"),
         mention.get("pollution"), mention.get("summary_pt")),
    )
    return cur.lastrowid


def merge_into(conn: sqlite3.Connection, incident_id: int, mention: dict) -> None:
    """Completa campos estáveis; vítimas são atualizadas com evidência após ligar a menção."""
    row = conn.execute("SELECT * FROM incident WHERE id = ?", (incident_id,)).fetchone()
    vessel = _first_vessel(mention)
    if row["vessel_name"] or row["imo"]:
        # A second collision participant must not donate its IMO/type to the primary vessel.
        vessel = next((v for v in mention.get("vessels", [])
                       if (v.get("imo") and v["imo"] == row["imo"])
                       or (v.get("name") and row["vessel_name"]
                           and normalize_name(v["name"]) == normalize_name(row["vessel_name"]))), {})

    def fill(current, new):
        return current if current not in (None, "") else (new or None)

    severity = max(row["severity"], mention["severity"], key=lambda s: SEVERITY_RANK.get(s, 0))
    # Coordenadas exatas substituem as aproximadas; entre duas aproximadas, fica a primeira.
    lat, lon, approx = row["lat"], row["lon"], row["coords_approx"]
    if mention.get("lat") is not None and (lat is None or (approx and not mention.get("coords_approximate"))):
        lat, lon, approx = mention["lat"], mention["lon"], bool(mention.get("coords_approximate"))
    conn.execute(
        """UPDATE incident SET event_date = ?, vessel_name = ?, imo = ?, vessel_type = ?,
               severity = ?, location = ?, country = ?, lat = ?, lon = ?, coords_approx = ?,
               cause_uncertain = ?, pollution = ?, updated_at = datetime('now')
           WHERE id = ?""",
        (reconciled_event_date(conn, row, mention), fill(row["vessel_name"], vessel.get("name")),
         fill(row["imo"], vessel.get("imo")), fill(row["vessel_type"], vessel.get("type")), severity,
         fill(row["location"], mention.get("location")), fill(row["country"], mention.get("country")),
         # Basta uma fonte levantar a dúvida (zona de conflito, causa não dita) para ela ficar:
         # uma fonte que só não repete a dúvida não prova que não foi ataque (caso de Ormuz, 5/11).
         lat, lon, approx, bool(row["cause_uncertain"] or mention.get("cause_uncertain")),
         row["pollution"] if row["pollution"] is not None else mention.get("pollution"),
         incident_id),
    )


def attach(conn: sqlite3.Connection, mention_id: int, mention: dict, judge=None) -> tuple[int, bool]:
    """Liga a menção a um acidente (existente ou novo). Devolve (incident_id, é_novo).

    judge(mention, candidatos) -> id ou None: segunda opinião (o Claude) para os casos
    em que as regras não decidem. Sem judge, valem só as regras.
    """
    match = find_match(conn, mention)
    if not match and judge:
        candidates = judge_candidates(conn, mention)
        if candidates:
            chosen = judge(mention, candidates)
            if chosen in {row["id"] for row in candidates}:
                match = (chosen, JUDGE_SCORE)
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
    apply_casualty_update(conn, incident_id, mention_id, mention, is_new=is_new)
    return incident_id, is_new
