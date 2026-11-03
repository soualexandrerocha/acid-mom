"""Triagem e extração: decide se um item fala de acidente com navio de carga e extrai os campos.

Duas etapas:
1. Filtro por palavras-chave (gratuito): descarta o que claramente não é acidente.
2. Uma chamada ao Claude com saída estruturada (JSON garantido pelo schema).
"""

import json
import logging
import os
import re

import anthropic

log = logging.getLogger(__name__)

# Modelo do Claude usado na extração; dá para trocar pela variável de ambiente ACIDMOM_MODEL.
MODEL = os.environ.get("ACIDMOM_MODEL") or "claude-haiku-5-5"

ACCIDENT_TERMS = re.compile(
    r"collision|collided|allision|aground|grounding|grounded|\bfire\b|blaze|explosion|exploded|"
    r"sank|sink|sinking|capsiz|listing|adrift|disabled|abandon|rescue|missing|flooding|"
    r"overboard|containers? lost|lost containers|hull breach|struck|casualty|"
    r"colis[aã]o|abalroa|encalh|inc[eê]ndio|naufr[aá]g|afund|explos[aã]o|[aà] deriva|"
    r"desaparecid|resgat|vazamento|cont[eê]ineres? ca[ií]|"
    # Outros idiomas (as buscas do Google News em fontes.toml): es, de, fr, it, nl, el, tr, ru, zh, ja, ko.
    r"colisi[oó]n|encall|naufragi|hundi|incendio|en llamas|explosi[oó]n|a la deriva|"
    r"kollision|kollidiert|gerammt|rammt|havarie|schiffsbrand|in brand|in flammen|feuer|gesunken|auf grund|"
    r"[ée]chou|incendie|en feu|naufrage|d[ée]rive|coul[ée]|"
    r"collisione|incagli|in fiamme|esplosione|affondat|"
    r"aanvaring|aan de grond|gezonken|explosie|op drift|"
    r"σύγκρουσ|προσάραξ|φωτιά|πυρκαγιά|βύθισ|έκρηξ|ναυάγι|"
    r"çarpış|karaya|yangın|batt[ıi]|patlama|"
    r"столкнов|на мел[ьи]|пожар|подрыв|затонул|взрыв|крушени|"
    r"碰撞|搁浅|起火|火灾|沉没|爆炸|衝突|座礁|火災|沈没|爆発|충돌|좌초|화재|침몰|폭발",
    re.IGNORECASE,
)


# Acidentes no embarque e desembarque do prático (escada de prático, escada combinada, guincho,
# lancha do prático). Essas notícias quase nunca usam as palavras de ACCIDENT_TERMS
# ("prático morre ao cair da escada"), por isso têm a própria lista. Só termos compostos:
# "pilot" ou "prático" sozinhos aparecem em muita notícia que não é do mar.
PILOT_TERMS = re.compile(
    r"pilot (ladder|boat|launch|hoist|transfer|boarding|cutter)|combination ladder|"
    r"pilot (dies|died|killed|falls|fell|injured|missing|crushed)|"
    r"(marine|maritime|harbou?r|port|sea|ship'?s?|river|bar) pilots?\b|"
    r"escada de pr[aá]tico|lancha d[eao]s? pr[aá]tic|praticagem|"
    r"pr[aá]tico (do porto|morre|morreu|cai|caiu|fica|ficou|ferid|desaparec|sofre)|"
    r"escala de pr[aá]ctico|lancha de pr[aá]cticos|practicaje|"
    r"pr[aá]ctico (del puerto|muere|muri[oó]|cae|cay[oó]|herido|desaparec)|"
    r"\blotsen?\b|lotsen(leiter|boot|versetz|tender)|"
    r"[ée]chelle de pilote|pilotine|pilote (maritime|du port|de port|de la station)|"
    r"biscaggina|pilotina|pilota del porto|scala del pilota|"
    r"loodsladder|loodsboot|loodstender|loodsvaartuig|beloodsing|"
    r"πλοηγ|ανεμόσκαλ|πιλοτίνα|"
    r"kılavuz kaptan|kılavuz botu|pilot merdiven|pilot botu|"
    r"лоцман|штормтрап|"
    r"引航员|引航梯|引水员|引航艇|领航员|水先人|水先案内|パイロットラダー|水先艇|"
    r"도선사|도선 ?사다리|도선선|파일럿 ?사다리",
    re.IGNORECASE,
)


def passes_keyword_filter(title: str, body: str) -> bool:
    text = f"{title}\n{body}"
    return bool(ACCIDENT_TERMS.search(text) or PILOT_TERMS.search(text))


# Ataques ficam fora do escopo. Muitas notícias (as do Google News) chegam só com o título,
# e sem o texto o Claude não percebe que "bateu numa mina" é um ataque; por isso o título
# é barrado antes. Só o título: o texto de uma matéria longa pode citar essas palavras de passagem.
ATTACK_TERMS = re.compile(
    r"\bmines?\b|\bmined\b|missile|\bdrones?\b|torpedo|projectile|houthi|\battack|\bstrikes? on\b|"
    r"pirat|hijack|\bseiz|\bboarded by\b|"
    r"\bminas?\b|m[ií]ssil|ataque|atacad|pirata|sequestr|apreendid|houthis?|"
    # Outros idiomas: minas, ataques, mísseis, drones e piratas.
    r"minen|seemine|zeemijn|νάρκ|mayın|\bмин(а|е|у|ы|ой)\b|触雷|水雷|機雷|기뢰|"
    r"angriff|attaque|attacco|aanval|επίθεσ|saldır|атак|袭击|遇袭|攻撃|공격|"
    r"misil|\brakete|\braket|füze|ракет|导弹|ミサイル|미사일|πύραυλ|"
    r"\bdron|drohne|дрон|беспилот|无人机|無人機|ドローン|드론|İHA|"
    r"korsan|пират|海盗|海賊|해적|πειρατ",
    re.IGNORECASE,
)


def is_attack(title: str) -> bool:
    return bool(ATTACK_TERMS.search(title))


EVENT_TYPES = [
    "collision", "contact", "grounding", "fire_explosion", "flooding_foundering",
    "capsizing", "loss_of_control", "hull_failure", "cargo_loss", "occupational_fatality",
    "pilot_transfer", "other",
]
SEVERITIES = ["very_serious", "serious", "less_serious", "marine_incident", "unknown"]


def _nullable(kind: str) -> dict:
    return {"anyOf": [{"type": kind}, {"type": "null"}]}


SCHEMA = {
    "type": "object",
    "properties": {
        "is_relevant": {"type": "boolean"},
        "reason": {"type": "string"},
        "vessels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "imo": {"type": "string"},
                    "type": {"type": "string"},
                    "flag": {"type": "string"},
                },
                "required": ["name", "imo", "type", "flag"],
                "additionalProperties": False,
            },
        },
        "event_date": {"type": "string"},
        "event_type": {"type": "string", "enum": EVENT_TYPES},
        "severity": {"type": "string", "enum": SEVERITIES},
        "location": {"type": "string"},
        "country": {"type": "string"},
        "lat": _nullable("number"),
        "lon": _nullable("number"),
        "coords_approximate": {"type": "boolean"},
        "cause_uncertain": {"type": "boolean"},
        "dead": _nullable("integer"),
        "missing": _nullable("integer"),
        "injured": _nullable("integer"),
        "pollution": _nullable("boolean"),
        "summary_pt": {"type": "string"},
        "evidence": {"type": "string"},
    },
    "required": [
        "is_relevant", "reason", "vessels", "event_date", "event_type", "severity", "location",
        "country", "lat", "lon", "coords_approximate", "cause_uncertain", "dead", "missing", "injured", "pollution", "summary_pt", "evidence",
    ],
    "additionalProperties": False,
}

SYSTEM = """You review maritime news items and navigational warnings for a tracker of accidents \
involving merchant CARGO ships (bulk carriers, container ships, tankers, general cargo, ro-ro, \
gas carriers, car carriers), anywhere at sea, including cabotage (cargo between ports of the same \
country).

is_relevant = true only if the item reports a specific accident or incident that happened to at \
least one such cargo ship. It is false for: fishing, passenger, leisure, naval or offshore-only \
vessels; inland waterway barges and pushboats on rivers and lakes; attacks of any kind (missiles, \
drones, mines, projectiles, piracy, armed boardings), even when they cause damage, fire or \
casualties; accidents with pilot boats or tugs that are not linked to a cargo ship; general market news, opinion, court cases or reports about old accidents with \
nothing new; exercises and routine navigational warnings. When false, fill the other fields with \
empty strings, "other", "unknown" or null.

Pilot transfer accidents are relevant: a marine pilot boarding or leaving a cargo ship who \
falls from (or with) the pilot ladder, combination ladder, accommodation ladder or pilot hoist; a \
ladder, rope, step or hoist that breaks or is rigged wrongly during a transfer, even if nobody is \
hurt; a pilot boat that strikes, is crushed against or capsizes alongside a cargo ship during the \
transfer. Use event_type "pilot_transfer" for them, list the cargo ship first in vessels (the pilot \
boat, if named, after it), and count the pilot among dead/missing/injured. A failure with no one \
hurt is a marine_incident.

Severity follows the IMO Casualty Investigation Code:
- very_serious: total loss of the ship, loss of life, or severe pollution.
- serious: fire, explosion, collision, grounding, contact, heavy weather or hull damage that \
immobilises the main engines, causes severe structural damage, any pollution, or a breakdown that \
needs towage or shore assistance.
- less_serious: a casualty that does not meet the two levels above.
- marine_incident: an occurrence that endangered the ship or people but caused no casualty.
- unknown: not enough information.

Rules:
- Use only what the text says. Never guess an IMO number; leave it empty if absent.
- vessels[].name is only the ship's own name (e.g. "Ever Given"). If the text does not give it, \
leave it empty; never write a description such as "MSC container ship" or "name not given".
- event_date is YYYY-MM-DD when the text gives or clearly implies it, otherwise empty.
- lat/lon in decimal degrees, south and west negative. If the text gives coordinates (navigational \
warnings often do), use them and set coords_approximate false. Otherwise, if it names a specific \
place (a port, strait, cape, island, city or small sea area), give that place's approximate \
coordinates and set coords_approximate true. If it names only a country or an ocean, or nothing, \
use null and set coords_approximate true.
- cause_uncertain: true when the ship had a fire, explosion, sinking or hull damage in an area of \
armed conflict (Persian Gulf, Strait of Hormuz, Gulf of Oman, Red Sea, Gulf of Aden, Black Sea) \
and the text does not state a non-hostile cause (such as an engine room fire, a collision or bad \
weather). Otherwise false.
- dead/missing/injured: numbers stated in the text, else null.
- summary_pt: one or two sentences in Brazilian Portuguese.
- evidence: the short quote from the text that best supports is_relevant."""


def build_prompt(title: str, body: str, source: str, published_at: str | None) -> str:
    return (
        f"Source: {source}\nPublished: {published_at or 'unknown'}\n"
        f"Title: {title}\n\nText:\n{body or '(only the title is available)'}"
    )


def extract(client: anthropic.Anthropic, title: str, body: str, source: str,
            published_at: str | None) -> dict | None:
    """Devolve o dict extraído, ou None se o modelo recusou ou a resposta veio incompleta."""
    # Se o modelo recusar por segurança, o servidor tenta de novo num modelo alternativo.
    # O Haiku não tem esse recurso.
    fallback = {} if "haiku" in MODEL else {
        "betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=SYSTEM,
        messages=[{"role": "user", "content": build_prompt(title, body, source, published_at)}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        **fallback,
    )
    if response.stop_reason != "end_turn":
        log.warning("Extração interrompida (%s): %s", response.stop_reason, title[:80])
        return None
    text = next(b.text for b in response.content if b.type == "text")
    return clean(json.loads(text))


# O modelo às vezes escreve uma descrição no lugar do nome ("MSC container ship (name not given)").
PLACEHOLDER_NAME = re.compile(
    r"not (given|named|identified|stated)|unknown|unnamed|unidentified|n[aã]o (informad|identificad)|\(",
    re.IGNORECASE,
)


def clean(data: dict) -> dict:
    for vessel in data.get("vessels", []):
        if PLACEHOLDER_NAME.search(vessel.get("name") or ""):
            vessel["name"] = ""
    return data


JUDGE_SYSTEM = """You decide whether a new report about a maritime accident describes the same
event as one of the incidents already recorded. Reports come from news in many languages, often
without the ship's name and with vague places ("Mediterranean Sea", "off Cyprus").

Treat it as the same event when the details fit together: same kind of accident, compatible place
and dates, and no contradiction in ship type, flag, crew numbers or rescued people. Different
places, a different ship name, or contradictory numbers mean different events. When in doubt,
answer null: a duplicate on the map is better than hiding a real accident."""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "same_incident_id": {"type": ["integer", "null"]},
        "reason": {"type": "string"},
    },
    "required": ["same_incident_id", "reason"],
    "additionalProperties": False,
}


def _describe(fields: dict) -> str:
    keys = ("event_date", "vessel_name", "vessel_type", "event_type", "location", "country",
            "lat", "lon", "dead", "missing", "summary")
    return "; ".join(f"{k}={fields[k]}" for k in keys if fields.get(k) not in (None, ""))


def same_event(client: anthropic.Anthropic, mention: dict, candidates: list) -> int | None:
    """Pergunta ao Claude se a menção é um dos acidentes candidatos. Devolve o id ou None."""
    vessel = (mention.get("vessels") or [{}])[0]
    new = _describe(mention | {"vessel_name": vessel.get("name"), "vessel_type": vessel.get("type"),
                               "summary": mention.get("summary_pt")})
    known = "\n".join(f"- id {row['id']}: {_describe(dict(row))}" for row in candidates)
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=1000,
        system=JUDGE_SYSTEM,
        messages=[{"role": "user", "content": f"New report:\n{new}\n\nRecorded incidents:\n{known}"}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
    )
    if response.stop_reason != "end_turn":
        return None
    answer = json.loads(next(b.text for b in response.content if b.type == "text"))
    log.info("Segunda opinião: %s (%s)", answer["same_incident_id"], answer["reason"][:120])
    return answer["same_incident_id"]
