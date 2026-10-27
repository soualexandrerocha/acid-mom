import json

import httpx
from pathlib import Path

from acidmom import collectors

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_rss_strips_html_and_reads_dates():
    items = collectors.parse_rss("Exemplo", (FIXTURES / "feed.xml").read_bytes())
    assert len(items) == 2
    first = items[0]
    assert first.url == "https://example.com/aground"
    assert first.body == "The bulk carrier Star Alpha ran aground near Santos."
    assert first.published_at == "2026-10-26T10:00:00+00:00"


def test_parse_gdelt():
    items = collectors.parse_gdelt(json.loads((FIXTURES / "gdelt.json").read_text()))
    assert items[0].source == "GDELT (news.example)"
    assert items[0].published_at == "2026-10-26T12:15:00+00:00"


def test_parse_navarea():
    items = collectors.parse_navarea(json.loads((FIXTURES / "navarea.json").read_text()))
    item = items[0]
    assert item.title.startswith("NAVAREA 4 1234/2026: WESTERN NORTH ATLANTIC.")
    assert "DISABLED AND ADRIFT" in item.body
    assert item.published_at == "2026-10-26T12:00:00+00:00"


def test_collect_all_survives_a_broken_source(monkeypatch):
    def boom(*_):
        raise RuntimeError("fora do ar")

    monkeypatch.setattr(collectors, "fetch_rss", boom)
    monkeypatch.setattr(collectors, "fetch_navarea",
                        lambda url: collectors.parse_navarea(json.loads((FIXTURES / "navarea.json").read_text())))
    config = {"rss": [{"nome": "X", "url": "http://x"}], "navarea": {"url": "http://n"}}
    assert len(collectors.collect_all(config)) == 1


def test_gdelt_retries_once_after_429(monkeypatch):
    responses = [
        httpx.Response(429, request=httpx.Request("GET", collectors.GDELT_URL)),
        httpx.Response(200, json=json.loads((FIXTURES / "gdelt.json").read_text()),
                       request=httpx.Request("GET", collectors.GDELT_URL)),
    ]
    monkeypatch.setattr(collectors, "GDELT_MIN_INTERVAL", 0)
    monkeypatch.setattr(httpx.Client, "get", lambda self, url, params=None: responses.pop(0))
    assert len(collectors.fetch_gdelt("x", 24)) == 1
    assert not responses
