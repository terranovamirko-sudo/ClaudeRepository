"""Notifiche opzionali: Telegram e webhook generico.

Restano inattive finche non vengono configurate, cosi il programma funziona
subito anche senza impostare nulla.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from .config import NotifyConfig


class NotifyError(RuntimeError):
    pass


def _post(url: str, data: bytes, content_type: str, timeout: int = 15) -> None:
    request = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status >= 300:
                raise NotifyError(f"risposta {response.status}")
    except urllib.error.HTTPError as exc:
        raise NotifyError(f"HTTP {exc.code}: {exc.read()[:200].decode('utf-8', 'replace')}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise NotifyError(str(exc)) from exc


def send_telegram(cfg: NotifyConfig, text: str) -> None:
    url = f"https://api.telegram.org/bot{cfg.telegram_bot_token}/sendMessage"
    payload = json.dumps({
        "chat_id": cfg.telegram_chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }).encode("utf-8")
    _post(url, payload, "application/json")


def send_webhook(cfg: NotifyConfig, text: str, extra: dict | None = None) -> None:
    payload = json.dumps({"text": text, **(extra or {})}).encode("utf-8")
    _post(cfg.webhook_url, payload, "application/json")


def notify(cfg: NotifyConfig, text: str, *, has_news: bool,
           extra: dict | None = None) -> list[str]:
    """Invia le notifiche configurate. Ritorna la lista dei problemi incontrati.

    Un errore di notifica non deve mai far fallire l'analisi: viene segnalato
    e basta.
    """
    if cfg.only_on_change and not has_news:
        return []

    problems: list[str] = []
    if cfg.telegram_bot_token and cfg.telegram_chat_id:
        try:
            send_telegram(cfg, text)
        except NotifyError as exc:
            problems.append(f"Telegram: {exc}")
    if cfg.webhook_url:
        try:
            send_webhook(cfg, text, extra)
        except NotifyError as exc:
            problems.append(f"Webhook: {exc}")
    return problems
