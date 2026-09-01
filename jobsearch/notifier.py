import os
import smtplib
from datetime import date
from email.mime.text import MIMEText

from dotenv import load_dotenv

from jobsearch.config import PATHS


def _build_body(pertinent: list[dict], threshold: int, report_path: str) -> str:
    lines = [
        f"Pipeline stage — {date.today()}",
        f"{len(pertinent)} offre(s) pertinente(s) (score ≥ {threshold}/10)",
        f"Rapport complet : {report_path}",
        "",
        "─" * 50,
        "",
    ]
    top = sorted(pertinent, key=lambda o: o["filter_result"].get("score", 0), reverse=True)[:5]
    for offer in top:
        fr = offer["filter_result"]
        score = fr.get("score", "?")
        title = offer.get("title", "Sans titre")
        company = offer.get("company", "") or "?"
        location = offer.get("location", "")
        url = offer.get("url", "")
        raison = fr.get("raison", "")
        lines.append(f"[{score}/10] {title} — {company}")
        if location:
            lines.append(f"  📍 {location}")
        if url:
            lines.append(f"  🔗 {url}")
        if raison:
            lines.append(f"  💬 {raison}")
        lines.append("")
    if len(pertinent) > 5:
        lines.append(f"... et {len(pertinent) - 5} autre(s) offre(s) dans le rapport.")
        lines.append("")
    lines.append("─" * 50)
    lines.append("Pour générer une candidature :")
    lines.append("  jobsearch --generate <offer_id>")
    return "\n".join(lines)


def send_notification(
    pertinent: list[dict],
    threshold: int,
    report_path: str,
) -> bool:
    """Sends an email digest if NOTIFY_EMAIL is configured. Returns True if sent."""
    load_dotenv(PATHS.env_path, override=True)
    recipient = os.environ.get("NOTIFY_EMAIL", "")
    if not recipient:
        return False

    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASSWORD", "")

    if not smtp_user or not smtp_pass:
        print("  Notification ignorée : SMTP_USER ou SMTP_PASSWORD manquant dans .env")
        return False

    subject = (
        f"[Stage Pipeline] {len(pertinent)} offre(s) pertinente(s) — {date.today()}"
        if pertinent
        else f"[Stage Pipeline] Aucune nouvelle offre pertinente — {date.today()}"
    )

    body = _build_body(pertinent, threshold, report_path)
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = recipient

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, [recipient], msg.as_string())
        return True
    except Exception as e:
        print(f"  Erreur d'envoi email : {e}")
        return False
