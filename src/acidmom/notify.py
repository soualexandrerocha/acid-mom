"""E-mails: aviso imediato de acidente muito grave e relatório semanal."""

import html
import logging
import os
import smtplib
import sqlite3
from email.message import EmailMessage

log = logging.getLogger(__name__)

SEVERITY_PT = {
    "very_serious": "muito grave",
    "serious": "grave",
    "less_serious": "menos grave",
    "marine_incident": "incidente marítimo",
    "unknown": "gravidade desconhecida",
}
EVENT_PT = {
    "collision": "colisão",
    "contact": "abalroamento (contato)",
    "grounding": "encalhe",
    "fire_explosion": "incêndio/explosão",
    "flooding_foundering": "alagamento/naufrágio",
    "capsizing": "emborcamento",
    "loss_of_control": "perda de propulsão ou governo",
    "hull_failure": "falha estrutural",
    "cargo_loss": "perda de carga",
    "occupational_fatality": "acidente de trabalho fatal",
    "other": "outro",
}
ALERT_SEVERITIES = ("very_serious",)
# Aviso imediato só para acidentes recentes; os mais antigos vão para o mapa e o relatório.
ALERT_MAX_AGE_DAYS = 3


def _sources(conn: sqlite3.Connection, incident_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT s.source, s.url, s.title FROM incident_mention im
           JOIN mention m ON m.id = im.mention_id
           JOIN source_item s ON s.id = m.source_item_id
           WHERE im.incident_id = ? ORDER BY s.published_at""",
        (incident_id,),
    ).fetchall()


def _victims(row: sqlite3.Row) -> str:
    parts = [f"{row[k]} {label}" for k, label in
             (("dead", "morto(s)"), ("missing", "desaparecido(s)"), ("injured", "ferido(s)")) if row[k]]
    return ", ".join(parts)


def format_incident(conn: sqlite3.Connection, row: sqlite3.Row) -> tuple[str, str]:
    """Texto puro e HTML de um acidente."""
    title = f"{row['vessel_name'] or 'Navio não identificado'}: {EVENT_PT.get(row['event_type'], row['event_type'])}"
    details = [
        ("Gravidade", SEVERITY_PT.get(row["severity"], row["severity"])),
        ("Data", row["event_date"] or "não informada"),
        ("Local", ", ".join(filter(None, [row["location"], row["country"]])) or "não informado"),
        ("Tipo de navio", row["vessel_type"] or "não informado"),
        ("IMO", row["imo"] or "não informado"),
        ("Vítimas", _victims(row) or "nenhuma informada"),
    ]
    sources = _sources(conn, row["id"])
    text = "\n".join([title, row["summary"] or "", *(f"{k}: {v}" for k, v in details), "Fontes:",
                      *(f"- {s['source']}: {s['url']}" for s in sources)])
    html_body = (
        f"<h3>{html.escape(title)}</h3><p>{html.escape(row['summary'] or '')}</p><ul>"
        + "".join(f"<li><b>{k}:</b> {html.escape(str(v))}</li>" for k, v in details)
        + "</ul><p>Fontes:</p><ul>"
        + "".join(f"<li><a href=\"{html.escape(s['url'])}\">{html.escape(s['source'])}</a>: "
                  f"{html.escape(s['title'])}</li>" for s in sources)
        + "</ul>"
    )
    return text, html_body


def send_email(subject: str, text: str, html_body: str) -> bool:
    """Envia por SMTP. Sem configuração, só mostra o e-mail no log (modo de teste)."""
    host, to = os.environ.get("SMTP_HOST"), os.environ.get("EMAIL_TO")
    if not host or not to:
        log.info("SMTP não configurado; e-mail não enviado.\nAssunto: %s\n%s", subject, text)
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("EMAIL_FROM") or os.environ["SMTP_USER"]
    msg["To"] = to
    msg.set_content(text)
    msg.add_alternative(f"<html><body>{html_body}</body></html>", subtype="html")
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT") or 587)) as smtp:
        smtp.starttls()
        smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        smtp.send_message(msg)
    return True


def send_alerts(conn: sqlite3.Connection) -> int:
    """Um e-mail por acidente muito grave ainda não avisado."""
    rows = conn.execute(
        f"SELECT * FROM incident WHERE alert_sent_at IS NULL AND severity IN "
        f"({','.join('?' * len(ALERT_SEVERITIES))}) "
        f"AND COALESCE(event_date, date(created_at)) >= date('now', ?) ORDER BY created_at",
        (*ALERT_SEVERITIES, f"-{ALERT_MAX_AGE_DAYS} days"),
    ).fetchall()
    for row in rows:
        text, html_body = format_incident(conn, row)
        subject = f"[Acid Mom] Acidente {SEVERITY_PT[row['severity']]}: {row['vessel_name'] or 'navio de carga'}"
        if send_email(subject, text, html_body):
            conn.execute("UPDATE incident SET alert_sent_at = datetime('now') WHERE id = ?", (row["id"],))
            conn.commit()
    return len(rows)


def weekly_report(conn: sqlite3.Connection, days: int = 7) -> tuple[str, str, str]:
    rows = conn.execute(
        "SELECT * FROM incident WHERE created_at >= datetime('now', ?) "
        "AND (event_date IS NULL OR event_date >= date('now', '-30 days')) ORDER BY "
        "CASE severity WHEN 'very_serious' THEN 0 WHEN 'serious' THEN 1 WHEN 'less_serious' THEN 2 "
        "WHEN 'marine_incident' THEN 3 ELSE 4 END, event_date DESC",
        (f"-{days} days",),
    ).fetchall()
    subject = f"[Acid Mom] Relatório semanal: {len(rows)} acidente(s) de carga nos últimos {days} dias"
    if not rows:
        return subject, "Nenhum acidente registrado no período.", "<p>Nenhum acidente registrado no período.</p>"
    parts = [format_incident(conn, row) for row in rows]
    return (subject, "\n\n".join(p[0] for p in parts), "<hr>".join(p[1] for p in parts))
