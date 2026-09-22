"""Invio delle email di avviso via SMTP (solo libreria standard).

La password si puo tenere fuori dal file di configurazione, in una variabile
d'ambiente: un file JSON finisce facilmente in un backup o in un repository.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from .config import EmailConfig


class EmailError(RuntimeError):
    """Configurazione incompleta o invio non riuscito."""


def resolve_password(cfg: EmailConfig) -> str:
    """Password dalla configurazione o, meglio, dalla variabile d'ambiente."""
    if cfg.password:
        return cfg.password
    if cfg.password_env:
        return os.environ.get(cfg.password_env, "")
    return ""


# Dal 16 settembre 2024 Microsoft ha dismesso l'autenticazione di base sugli
# account Outlook.com/Hotmail/Live personali, e SMTP risponde "535 5.7.139".
# Il comportamento non e uniforme: su qualche casella piu vecchia l'invio puo
# ancora passare, mentre su quelle create di recente e disattivato per default.
# Per questo e un avviso e non un errore bloccante: sconsigliamo di costruirci
# sopra, senza dichiarare impossibile cio che su qualche account funziona.
# Ricevere su un indirizzo Outlook, in ogni caso, funziona sempre.
LEGACY_MICROSOFT_DOMAINS = ("outlook.com", "outlook.it", "hotmail.com", "hotmail.it",
                            "live.com", "live.it", "msn.com")


def warnings(cfg: EmailConfig) -> list[str]:
    """Problemi probabili ma non certi: non bloccano l'invio, lo prevengono."""
    notes: list[str] = []
    sender = (cfg.sender or cfg.username).lower()
    if sender.endswith(LEGACY_MICROSOFT_DOMAINS):
        notes.append(
            f"il mittente {sender} e un account Microsoft personale: dal 16 settembre 2024 "
            "l'autenticazione SMTP con password (comprese le password per app) e disattivata "
            "per default, e l'invio viene di norma rifiutato con un errore 535. Su qualche "
            "casella piu vecchia puo ancora funzionare, ma non e una base solida per un "
            "programma che deve girare per mesi: meglio un account Gmail dedicato con "
            "password per le app, o un servizio SMTP come Brevo. Ricevere su Outlook "
            "funziona in ogni caso."
        )
    if cfg.sender and cfg.username and cfg.sender.lower() != cfg.username.lower():
        notes.append(
            f"il mittente ({cfg.sender}) e diverso dall'utenza autenticata ({cfg.username}): "
            "molti provider rifiutano l'invio, e i controlli antispam del destinatario "
            "possono spostare il messaggio nella posta indesiderata."
        )
    return notes


# Valori segnaposto dei file di configurazione di esempio. Se restano cosi
# l'invio fallisce piu avanti, con un errore SMTP che non aiuta a capire.
PLACEHOLDER_MARKERS = ("INDIRIZZO-GMAIL-DEDICATO", "TUO-INDIRIZZO", "DA-COMPILARE")


def _is_placeholder(value: str) -> bool:
    return any(marker in value for marker in PLACEHOLDER_MARKERS)


def check_config(cfg: EmailConfig) -> list[str]:
    """Elenca cosa manca per poter inviare. Lista vuota = pronto."""
    problems: list[str] = []
    for campo, valore in (("username", cfg.username), ("sender", cfg.sender)):
        if valore and _is_placeholder(valore):
            problems.append(f"{campo} contiene ancora il segnaposto del file di esempio "
                            f"({valore}): sostituiscilo col tuo indirizzo")
    for destinatario in cfg.recipients:
        if _is_placeholder(destinatario):
            problems.append(f"il destinatario {destinatario} e ancora un segnaposto")
    if not cfg.smtp_host:
        problems.append("manca smtp_host")
    if not cfg.recipients:
        problems.append("manca almeno un destinatario in recipients")
    if not cfg.username and cfg.security != "none":
        problems.append("manca username")
    if not resolve_password(cfg) and cfg.security != "none":
        problems.append(f"manca la password (impostala in email.password oppure "
                        f"nella variabile d'ambiente {cfg.password_env})")
    if cfg.security not in ("starttls", "ssl", "none"):
        problems.append(f"security deve essere starttls, ssl o none (trovato: {cfg.security})")
    return problems


def build_message(cfg: EmailConfig, subject: str, text_body: str,
                  html_body: str | None = None) -> EmailMessage:
    message = EmailMessage()
    sender = cfg.sender or cfg.username
    message["From"] = formataddr((cfg.sender_name, sender)) if cfg.sender_name else sender
    message["To"] = ", ".join(cfg.recipients)
    prefix = f"{cfg.subject_prefix} " if cfg.subject_prefix else ""
    message["Subject"] = f"{prefix}{subject}"
    message.set_content(text_body)
    if html_body:
        message.add_alternative(html_body, subtype="html")
    return message


def send(cfg: EmailConfig, subject: str, text_body: str,
         html_body: str | None = None) -> None:
    """Invia una email. Solleva EmailError con un messaggio comprensibile."""
    problems = check_config(cfg)
    if problems:
        raise EmailError("configurazione email incompleta: " + "; ".join(problems))

    message = build_message(cfg, subject, text_body, html_body)
    password = resolve_password(cfg)
    context = ssl.create_default_context()

    try:
        if cfg.security == "ssl":
            server = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port,
                                      timeout=cfg.timeout, context=context)
        else:
            server = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=cfg.timeout)
        with server:
            server.ehlo()
            if cfg.security == "starttls":
                server.starttls(context=context)
                server.ehlo()
            if cfg.username and password:
                server.login(cfg.username, password)
            server.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise EmailError(
            f"il server ha rifiutato le credenziali ({exc.smtp_code}). "
            "Molti provider non accettano piu la password normale dell'account: "
            "serve una password per le app generata dalle impostazioni di sicurezza."
        ) from exc
    except smtplib.SMTPException as exc:
        raise EmailError(f"invio non riuscito: {exc}") from exc
    except OSError as exc:
        raise EmailError(f"impossibile contattare {cfg.smtp_host}:{cfg.smtp_port} — {exc}") from exc


def send_test(cfg: EmailConfig) -> None:
    """Email di prova, per verificare la configurazione senza aspettare un'occasione."""
    send(cfg,
         "Prova di configurazione",
         "Se stai leggendo questo messaggio, l'invio delle email funziona.\n\n"
         "Da adesso riceverai un avviso solo quando compare un'auto che supera "
         "le soglie che hai impostato.\n",
         "<p>Se stai leggendo questo messaggio, l'invio delle email funziona.</p>"
         "<p>Da adesso riceverai un avviso solo quando compare un'auto che supera "
         "le soglie che hai impostato.</p>")
