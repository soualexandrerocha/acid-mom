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
    r"desaparecid|resgat|vazamento|cont[eê]ineres? ca[ií]",
    re.IGNORECASE,
)


def passes_keyword_filter(title: str, body: str) -> bool:
    return bool(ACCIDENT_TERMS.search(f"{title}\n{body}"))


# Ataques ficam fora do escopo. Muitas notícias (as do Google News) chegam só com o título,
# e sem o texto o Claude não percebe que "bateu numa mina" é um ataque; por isso o título
# é barrado antes. Só o título: o texto de uma matéria longa pode citar essas palavras de passagem.
ATTACK_TERMS = re.compile(
    r"\bmines?\b|\bmined\b|missile|\bdrones?\b|torpedo|projectile|houthi|\battack|\bstrikes? on\b|"
    r"pirat|hijack|\bseiz|\bboarded by\b|"
    r"\bminas?\b|m[ií]ssil|ataque|atacad|pirata|sequestr|apreendid|houthis?",
    re.IGNORECASE,
)


def is_attack(title: str) -> bool:
    return bool(ATTACK_TERMS.search(title))


EVENT_TYPES = [
    "collision", "contact", "grounding", "fire_explosion", "flooding_foundering",
    "capsizing", "loss_of_control", "hull_failure", "cargo_loss", "occupational_fatality", "other",
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
casualties; general market news, opinion, court cases or reports about old accidents with \
nothing new; exercises and routine navigational warnings. When false, fill the other fields with \
empty strings, "other", "unknown" or null.

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
    return json.loads(text)
