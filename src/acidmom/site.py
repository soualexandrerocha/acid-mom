"""Página do mapa: um arquivo HTML com o mapa mundial e a lista dos acidentes recentes."""

import html
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .notify import EVENT_PT, SEVERITY_PT, _sources, _victims

DAYS = 90

SEVERITY_COLOR = {
    "very_serious": "#c62828",
    "serious": "#ef6c00",
    "less_serious": "#f9a825",
    "marine_incident": "#1565c0",
    "unknown": "#757575",
}


def incidents_for_map(conn: sqlite3.Connection, days: int = DAYS) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM incident WHERE COALESCE(event_date, date(created_at)) >= date('now', ?) "
        "ORDER BY COALESCE(event_date, date(created_at)) DESC, id DESC",
        (f"-{days} days",),
    ).fetchall()
    return [
        {
            "title": row["vessel_name"] or "Navio não identificado",
            "event": EVENT_PT.get(row["event_type"], row["event_type"]),
            "severity": row["severity"],
            "severity_pt": SEVERITY_PT.get(row["severity"], row["severity"]),
            "date": row["event_date"] or f"{row['created_at'][:10]} (data da notícia)",
            "place": ", ".join(filter(None, [row["location"], row["country"]])) or "local não informado",
            "vessel_type": row["vessel_type"] or "",
            "victims": _victims(row),
            "summary": row["summary"] or "",
            "lat": row["lat"],
            "lon": row["lon"],
            "approx": bool(row["coords_approx"]),
            "uncertain": bool(row["cause_uncertain"]),
            "sources": [{"name": s["source"], "url": s["url"]} for s in _sources(conn, row["id"])],
        }
        for row in rows
    ]


PAGE = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Acid Mom: acidentes com navios de carga</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  :root { --bg: #ffffff; --fg: #1f1f1f; --muted: #616161; --line: #e0e0e0; --card: #f7f7f7; }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #121212; --fg: #ececec; --muted: #a8a8a8; --line: #333333; --card: #1d1d1d; }
  }
  * { box-sizing: border-box; }
  body { margin: 0; font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; background: var(--bg); color: var(--fg); }
  header { padding: 16px; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  header p { margin: 0; color: var(--muted); font-size: 13px; }
  #map { height: 55vh; min-height: 320px; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
  .legend { display: flex; flex-wrap: wrap; gap: 12px; padding: 10px 16px; font-size: 13px; color: var(--muted); }
  .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 4px; vertical-align: middle; }
  main { padding: 0 16px 32px; max-width: 760px; }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 12px; margin: 12px 0; }
  .card h2 { font-size: 16px; margin: 0 0 4px; }
  .meta { color: var(--muted); font-size: 13px; }
  .card p { margin: 8px 0; }
  a { color: inherit; }
  .leaflet-popup-content { font: 13px/1.4 system-ui, sans-serif; }
</style>
</head>
<body>
<header>
  <h1>Acidentes com navios de carga</h1>
  <p>Últimos __DAYS__ dias · __COUNT__ acidente(s) · atualizado em __UPDATED__ (UTC)</p>
</header>
<div id="map"></div>
<div class="legend">__LEGEND__ <span>Círculo tracejado: posição aproximada</span></div>
<main id="list"></main>
<script>
const incidents = __DATA__;
const colors = __COLORS__;
const UNCERTAIN = "Causa incerta: zona de conflito, pode ter sido ataque";
const map = L.map("map", { worldCopyJump: true }).setView([20, 0], 2);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 12,
  attribution: "&copy; colaboradores do OpenStreetMap",
}).addTo(map);

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const describe = (i) =>
  `<b>${esc(i.title)}</b>: ${esc(i.event)}<br>` +
  `<span>${esc(i.severity_pt)} · ${esc(i.date)}</span><br>` +
  `<span>${esc(i.place)}${i.approx ? " (posição aproximada)" : ""}</span>` +
  (i.victims ? `<br><span>${esc(i.victims)}</span>` : "") +
  (i.uncertain ? `<br><span>${UNCERTAIN}</span>` : "");

const list = document.getElementById("list");
incidents.forEach((i) => {
  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML =
    `<h2>${esc(i.title)}: ${esc(i.event)}</h2>` +
    `<div class="meta"><span class="dot" style="background:${colors[i.severity]}"></span>` +
    `${esc(i.severity_pt)} · ${esc(i.date)} · ${esc(i.place)}${i.vessel_type ? " · " + esc(i.vessel_type) : ""}</div>` +
    (i.victims ? `<div class="meta">${esc(i.victims)}</div>` : "") +
    (i.uncertain ? `<div class="meta">${UNCERTAIN}</div>` : "") +
    `<p>${esc(i.summary)}</p>` +
    `<div class="meta">Fontes: ${i.sources.map((s) => `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name)}</a>`).join(", ")}</div>`;
  list.appendChild(card);
  if (i.lat !== null && i.lon !== null) {
    L.circleMarker([i.lat, i.lon], {
      radius: 8, color: colors[i.severity], fillColor: colors[i.severity], fillOpacity: 0.7,
      weight: 2, dashArray: i.approx ? "4 3" : null,
    }).addTo(map).bindPopup(describe(i)).on("click", () => card.scrollIntoView({ behavior: "smooth", block: "nearest" }));
  }
});
if (!incidents.length) list.innerHTML = "<p>Nenhum acidente registrado no período.</p>";
</script>
</body>
</html>
"""


def build_page(conn: sqlite3.Connection, out_dir: str | Path, days: int = DAYS) -> Path:
    incidents = incidents_for_map(conn, days)
    legend = " ".join(
        f'<span><span class="dot" style="background:{color}"></span>{html.escape(SEVERITY_PT[key])}</span>'
        for key, color in SEVERITY_COLOR.items()
    )
    # "</" dentro do JSON fecharia a tag <script> antes da hora.
    data = json.dumps(incidents, ensure_ascii=False).replace("</", "<\\/")
    page = (PAGE.replace("__DAYS__", str(days))
            .replace("__COUNT__", str(len(incidents)))
            .replace("__UPDATED__", datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M"))
            .replace("__LEGEND__", legend)
            .replace("__COLORS__", json.dumps(SEVERITY_COLOR))
            .replace("__DATA__", data))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(page, encoding="utf-8")
    return out / "index.html"
