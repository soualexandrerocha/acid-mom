"""Linha de comando.

    acidmom coletar     baixa itens novos de todas as fontes
    acidmom processar   filtra, extrai com o Claude e junta em acidentes
    acidmom avisar      envia e-mail para cada acidente muito grave ainda não avisado
    acidmom rodar       coletar + processar + avisar + mapa (o que o agendador chama)
    acidmom semanal     envia o relatório dos últimos 7 dias
    acidmom mapa        gera a página do mapa (site/index.html)
"""

import argparse
import logging
import os
import tomllib
from datetime import datetime, timedelta, timezone

import anthropic

from . import collectors, db, dedup, extract, notify, site

log = logging.getLogger("acidmom")

# Itens publicados há mais tempo que isso não são acidentes novos (ex.: avisos náuticos antigos ainda ativos).
MAX_AGE = timedelta(days=30)


def is_too_old(published_at: str | None) -> bool:
    if not published_at:
        return False
    return datetime.fromisoformat(published_at) < datetime.now(timezone.utc) - MAX_AGE


def cmd_collect(conn, config) -> None:
    items = collectors.collect_all(config)
    log.info("Coletados %d itens, %d novos", len(items), db.save_items(conn, items))


def cmd_process(conn, limit: int) -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.warning("ANTHROPIC_API_KEY não configurada; os itens coletados ficam para a próxima rodada.")
        return
    rows = conn.execute(
        "SELECT * FROM source_item WHERE status = 'new' ORDER BY id LIMIT ?", (limit,)
    ).fetchall()
    client = None
    counts = {"too_old": 0, "filtered_out": 0, "attack": 0, "not_relevant": 0, "extracted": 0, "error": 0, "new_incidents": 0}
    for row in rows:
        if is_too_old(row["published_at"]):
            status = "too_old"
        elif not extract.passes_keyword_filter(row["title"], row["body"]):
            status = "filtered_out"
        elif extract.is_attack(row["title"]):
            status = "attack"
        else:
            client = client or anthropic.Anthropic()
            try:
                data = extract.extract(client, row["title"], row["body"], row["source"], row["published_at"])
            except anthropic.APIError as exc:
                log.warning("Erro na API para o item %d: %s", row["id"], exc)
                data = None
            if data is None:
                status = "error"
            elif not data["is_relevant"]:
                status = "not_relevant"
            else:
                mention_id = db.save_mention(conn, row["id"], data)
                _, is_new = dedup.attach(conn, mention_id, data)
                counts["new_incidents"] += is_new
                status = "extracted"
        counts[status] += 1
        conn.execute("UPDATE source_item SET status = ? WHERE id = ?", (status, row["id"]))
        conn.commit()
    log.info("Processados %d itens: %s", len(rows), counts)


def cmd_alert(conn) -> None:
    log.info("Avisos de acidente muito grave: %d", notify.send_alerts(conn))


def cmd_weekly(conn) -> None:
    notify.send_email(*notify.weekly_report(conn))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="acidmom", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("comando", choices=["coletar", "processar", "avisar", "rodar", "semanal", "mapa"])
    parser.add_argument("--banco", default="data/acidmom.sqlite", help="arquivo do banco SQLite")
    parser.add_argument("--fontes", default="fontes.toml", help="arquivo de configuração das fontes")
    parser.add_argument("--site", default="site", help="pasta onde a página do mapa é gerada")
    parser.add_argument("--limite", type=int, default=300, help="máximo de itens processados por vez")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    conn = db.connect(args.banco)
    if args.comando in ("coletar", "rodar"):
        with open(args.fontes, "rb") as f:
            cmd_collect(conn, tomllib.load(f))
    if args.comando in ("processar", "rodar"):
        cmd_process(conn, args.limite)
    if args.comando in ("avisar", "rodar"):
        cmd_alert(conn)
    if args.comando == "semanal":
        cmd_weekly(conn)
    if args.comando in ("mapa", "rodar"):
        log.info("Mapa gerado em %s", site.build_page(conn, args.site))


if __name__ == "__main__":
    main()
