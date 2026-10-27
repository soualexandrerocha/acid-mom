"""Coletores: cada fonte vira uma lista de SourceItem.

Cada coletor tem uma função `parse_*` (pura, testada com arquivos de exemplo)
e uma função `fetch_*` que só baixa o conteúdo e chama o parse.
"""

import logging
import re
import time
from datetime import datetime, timezone

import feedparser
import httpx

from .db import SourceItem

log = logging.getLogger(__name__)

USER_AGENT = "acid-mom/0.1 (rastreador de acidentes maritimos; github.com/soualexandrerocha/acid-mom)"
TIMEOUT = 30


def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True)


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


# --- RSS -------------------------------------------------------------------

def parse_rss(source: str, content: bytes | str) -> list[SourceItem]:
    feed = feedparser.parse(content)
    items = []
    for entry in feed.entries:
        published = None
        if entry.get("published_parsed"):
            published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).isoformat()
        body = entry.get("summary") or ""
        if entry.get("content"):
            body = entry.content[0].get("value", body)
        # Buscas do Google News trazem notícias de vários sites; guarda o nome do site original.
        publisher = (entry.get("source") or {}).get("title")
        items.append(SourceItem(
            source=f"{source} ({publisher})" if publisher else source,
            url=entry.get("link", ""),
            title=_strip_html(entry.get("title", "")),
            body=_strip_html(body)[:4000],
            published_at=published,
        ))
    return items


def fetch_rss(name: str, url: str) -> list[SourceItem]:
    with _client() as client:
        resp = client.get(url)
        resp.raise_for_status()
    return parse_rss(name, resp.content)


# --- GDELT -----------------------------------------------------------------

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
# O GDELT bloqueia (HTTP 429) quem faz pedidos com menos de alguns segundos de intervalo.
GDELT_MIN_INTERVAL = 10
_last_gdelt_request = 0.0


def parse_gdelt(data: dict) -> list[SourceItem]:
    items = []
    for art in data.get("articles", []):
        published = None
        if seen := art.get("seendate"):  # ex.: 20261026T121500Z
            published = datetime.strptime(seen, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()
        items.append(SourceItem(
            source=f"GDELT ({art.get('domain', '?')})",
            url=art.get("url", ""),
            title=art.get("title", "").strip(),
            published_at=published,
        ))
    return items


def fetch_gdelt(query: str, hours: int) -> list[SourceItem]:
    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": 250,
        "timespan": f"{hours}h",
        "sort": "datedesc",
    }
    global _last_gdelt_request
    with _client() as client:
        for attempt in range(2):
            time.sleep(max(0.0, _last_gdelt_request + GDELT_MIN_INTERVAL - time.monotonic()))
            resp = client.get(GDELT_URL, params=params)
            _last_gdelt_request = time.monotonic()
            if resp.status_code != 429 or attempt:
                break
            log.info("GDELT pediu para esperar; tentando de novo")
        resp.raise_for_status()
    # O GDELT responde texto puro (não JSON) quando a consulta é inválida.
    if not resp.headers.get("content-type", "").startswith("application/json"):
        raise ValueError(f"GDELT respondeu sem JSON: {resp.text[:200]}")
    return parse_gdelt(resp.json())


# --- Avisos náuticos NAVAREA (NGA MSI) -------------------------------------

def parse_navarea(data: dict) -> list[SourceItem]:
    items = []
    for w in data.get("broadcast-warn", []):
        ident = f"NAVAREA {w.get('navArea', '?')} {w.get('msgNumber', '?')}/{w.get('msgYear', '?')}"
        text = (w.get("text") or "").strip()
        issued = None
        if raw := w.get("issueDate"):  # ex.: 261200Z OCT 2026
            try:
                issued = datetime.strptime(raw, "%d%H%MZ %b %Y").replace(tzinfo=timezone.utc).isoformat()
            except ValueError:
                pass
        items.append(SourceItem(
            source="NGA NAVAREA",
            url=f"https://msi.nga.mil/NavWarnings#{ident.replace(' ', '-')}",
            title=f"{ident}: {text.splitlines()[0] if text else ''}"[:200],
            body=text,
            published_at=issued,
        ))
    return items


def fetch_navarea(url: str) -> list[SourceItem]:
    with _client() as client:
        resp = client.get(url)
        resp.raise_for_status()
    return parse_navarea(resp.json())


# --- Todas as fontes -------------------------------------------------------

def collect_all(config: dict) -> list[SourceItem]:
    """Roda todos os coletores. Uma fonte com erro não derruba as outras."""
    jobs = [(f"RSS {f['nome']}", fetch_rss, (f["nome"], f["url"])) for f in config.get("rss", [])]
    if gdelt := config.get("gdelt"):
        jobs += [("GDELT", fetch_gdelt, (q, gdelt.get("horas", 24))) for q in gdelt.get("consultas", [])]
    if navarea := config.get("navarea"):
        jobs.append(("NAVAREA", fetch_navarea, (navarea["url"],)))

    items: list[SourceItem] = []
    for label, func, args in jobs:
        try:
            found = func(*args)
            log.info("%s: %d itens", label, len(found))
            items += found
        except Exception as exc:  # noqa: BLE001 - registra e segue para a próxima fonte
            log.warning("%s falhou: %s", label, exc)
    return items

