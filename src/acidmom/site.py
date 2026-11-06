"""Página do mapa: um arquivo HTML com o mapa mundial e a lista dos acidentes recentes."""

import html
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .notify import EVENT_PT, SEVERITY_PT, _sources, _victims
from .regioes import SEM_LOCAL, contar, regiao_da_linha

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
            "region": regiao_da_linha(row),
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
  :root { --bg: #ffffff; --fg: #1f1f1f; --muted: #616161; --line: #e0e0e0; --card: #f7f7f7; --bar: #1565c0; }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #121212; --fg: #ececec; --muted: #a8a8a8; --line: #333333; --card: #1d1d1d; --bar: #64a0e6; }
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
  section.regions { padding: 8px 16px 0; max-width: 760px; }
  section.regions h2 { font-size: 16px; margin: 8px 0 2px; }
  section.regions > p { margin: 0 0 8px; color: var(--muted); font-size: 13px; }
  table.reg { border-collapse: collapse; width: 100%; font-size: 14px; }
  table.reg th { text-align: left; font-weight: 600; color: var(--muted); font-size: 12px; padding: 4px 6px; border-bottom: 1px solid var(--line); }
  table.reg td { padding: 5px 6px; border-bottom: 1px solid var(--line); vertical-align: middle; }
  table.reg td.n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .bar { height: 8px; margin-top: 4px; background: var(--bar); border-radius: 0 4px 4px 0; min-width: 2px; }
  table.reg tr.none td { color: var(--muted); }
  table.reg tr.none .bar { background: var(--line); }
  table.reg tr[data-region] { cursor: pointer; }
  table.reg tr.active td { background: var(--card); }
  #filtro { font-size: 13px; color: var(--muted); margin: 12px 0 0; }
  #filtro button { font: inherit; color: inherit; background: none; border: 1px solid var(--line); border-radius: 6px; padding: 2px 8px; cursor: pointer; }
</style>
</head>
<body>
<header>
  <h1>Acidentes com navios de carga</h1>
  <p>Últimos __DAYS__ dias · __COUNT__ acidente(s) · atualizado em __UPDATED__ (UTC)</p>
</header>
<div id="map"></div>
<div class="legend">__LEGEND__ <span>Círculo tracejado: posição aproximada</span></div>
<section class="regions">
  <h2>Acidentes por região</h2>
  <p>Mesmo período do mapa. Toque numa região para ver só os acidentes dela na lista.</p>
  <table class="reg">
    <thead><tr><th>Região</th><th class="n">Acidentes</th><th class="n">Muito graves</th></tr></thead>
    <tbody>__REGIONS__</tbody>
  </table>
</section>
<main>
<p id="filtro" hidden></p>
<div id="list"></div>
</main>
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
  card.dataset.region = i.region;
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

const filtro = document.getElementById("filtro");
let ativa = null;
function filtrar(regiao) {
  ativa = ativa === regiao ? null : regiao;
  list.querySelectorAll(".card").forEach((c) => { c.hidden = ativa !== null && c.dataset.region !== ativa; });
  document.querySelectorAll("tr[data-region]").forEach((tr) => tr.classList.toggle("active", tr.dataset.region === ativa));
  filtro.hidden = ativa === null;
  if (ativa !== null) {
    filtro.innerHTML = `Mostrando só: <b>${esc(ativa)}</b> <button type="button">mostrar todos</button>`;
    filtro.querySelector("button").onclick = () => filtrar(ativa);
    filtro.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}
document.querySelectorAll("tr[data-region]").forEach((tr) => tr.addEventListener("click", () => filtrar(tr.dataset.region)));
</script>
</body>
</html>
"""


def regions_table(incidents: list[dict]) -> str:
    """Linhas da tabela por região, com uma barra proporcional ao número de acidentes."""
    linhas = contar(incidents)
    maior = max((l["total"] for l in linhas), default=1)
    out = []
    for l in linhas:
        nome = html.escape(l["region"])
        classe = ' class="none"' if l["region"] == SEM_LOCAL else ""
        largura = round(100 * l["total"] / maior)
        out.append(
            f'<tr{classe} data-region="{nome}" title="{nome}: {l["total"]} acidente(s)">'
            f'<td>{nome}<div class="bar" style="width:{largura}%" aria-hidden="true"></div></td>'
            f'<td class="n">{l["total"]}</td><td class="n">{l["very_serious"]}</td></tr>'
        )
    return "".join(out)


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
            .replace("__REGIONS__", regions_table(incidents))
            .replace("__COLORS__", json.dumps(SEVERITY_COLOR))
            .replace("__DATA__", data))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(page, encoding="utf-8")
    return out / "index.html"
