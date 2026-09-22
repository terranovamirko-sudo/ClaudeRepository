"""Download dei risultati dall'inventario Tesla.

Tre backend:
  * ``http``    - richiesta diretta all'API JSON (solo libreria standard).
  * ``browser`` - Chromium via Playwright: esegue il JavaScript della pagina e
                  intercetta la risposta dell'API. Serve quando Akamai (il CDN
                  di Tesla) risponde con una sfida anti-bot.
  * ``file``    - legge un JSON gia salvato: utile per test offline e per
                  rianalizzare uno snapshot senza ripetere il download.
"""

from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
from typing import Any

from .config import Config, SearchConfig

API_URL = "https://www.tesla.com/inventory/api/v4/inventory-results"
PAGE_URL_TEMPLATE = (
    "https://www.tesla.com/{market_lang}/inventory/{condition}/{model}"
    "?arrangeby={arrangeby}&zip={zip}&PaymentType={payment}"
)


class FetchError(RuntimeError):
    """Errore di rete o di protocollo durante il download."""


class BotChallengeError(FetchError):
    """Tesla/Akamai ha risposto con una sfida anti-bot invece dei dati.

    Capita tipicamente da IP di datacenter (server, CI, VPS). Da una normale
    connessione domestica il backend ``http`` funziona senza problemi.
    """


def page_url(search: SearchConfig) -> str:
    """URL della pagina pubblica corrispondente alla ricerca configurata."""
    market_lang = f"{search.language}_{search.market}"
    return PAGE_URL_TEMPLATE.format(
        market_lang=market_lang,
        condition=search.condition,
        model=search.model,
        arrangeby=search.arrangeby.lower(),
        zip=search.zip_code,
        payment=search.payment_type,
    )


def build_query(search: SearchConfig, offset: int) -> dict[str, Any]:
    """Payload della query accettato dall'endpoint inventory-results."""
    return {
        "query": {
            "model": search.model,
            "condition": search.condition,
            "options": {},
            "arrangeby": search.arrangeby,
            "order": search.order,
            "market": search.market,
            "language": search.language,
            "super_region": "north america",  # costante richiesta dall'API
            "zip": search.zip_code,
            "range": search.range_km,
        },
        "offset": offset,
        "count": search.count,
        "outsideOffset": 0,
        "outsideSearch": False,
    }


def _headers(cfg: Config, referer: str) -> dict[str, str]:
    """Header che riproducono una chiamata XHR fatta dalla pagina Tesla."""
    return {
        "User-Agent": cfg.fetch.user_agent,
        "Accept": "*/*",
        "Accept-Language": f"{cfg.search.language}-{cfg.search.market},"
                           f"{cfg.search.language};q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Referer": referer,
        "Origin": "https://www.tesla.com",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Connection": "keep-alive",
    }


def _decode(raw: bytes, encoding: str) -> str:
    if encoding == "gzip":
        raw = gzip.decompress(raw)
    elif encoding == "deflate":
        raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw.decode("utf-8", errors="replace")


def _looks_like_challenge(text: str) -> bool:
    """Riconosce le risposte di sfida anti-bot di Akamai."""
    probe = text[:800].lower()
    markers = ("cpr_chlge", "access denied", "sec-if-cpt-container",
               "powered and protected by", "_abck")
    return any(m in probe for m in markers)


# --- backend http ------------------------------------------------------------

def _fetch_page_http(cfg: Config, offset: int) -> dict[str, Any]:
    query = json.dumps(build_query(cfg.search, offset), separators=(",", ":"))
    url = f"{API_URL}?{urllib.parse.urlencode({'query': query})}"
    request = urllib.request.Request(url, headers=_headers(cfg, page_url(cfg.search)))

    last_error: Exception | None = None
    for attempt in range(cfg.fetch.retries):
        if attempt:
            time.sleep(cfg.fetch.retry_backoff ** attempt)
        try:
            with urllib.request.urlopen(request, timeout=cfg.fetch.timeout) as response:
                body = _decode(response.read(), response.headers.get("Content-Encoding", ""))
        except urllib.error.HTTPError as exc:
            detail = _decode(exc.read(), exc.headers.get("Content-Encoding", "") if exc.headers else "")
            if exc.code in (403, 429) and _looks_like_challenge(detail):
                raise BotChallengeError(
                    f"Tesla ha risposto {exc.code} con una sfida anti-bot. "
                    "Esegui il programma da una connessione domestica, oppure usa "
                    "--backend browser (richiede: pip install playwright && playwright install chromium)."
                ) from exc
            last_error = FetchError(f"HTTP {exc.code} da Tesla: {detail[:200]}")
            continue
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = FetchError(f"Errore di rete: {exc}")
            continue

        if _looks_like_challenge(body):
            raise BotChallengeError(
                "Tesla ha restituito una pagina di verifica anti-bot invece dei dati. "
                "Riprova da una connessione domestica o usa --backend browser."
            )
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            last_error = FetchError(f"Risposta non JSON da Tesla: {body[:200]}")
            continue

    raise last_error or FetchError("Download fallito senza dettagli")


# --- backend browser ---------------------------------------------------------

def _fetch_browser(cfg: Config) -> dict[str, Any]:
    """Apre la pagina con Chromium e intercetta la risposta dell'API.

    Il browser esegue il JavaScript della pagina, quindi supera le sfide
    anti-bot che bloccano la richiesta HTTP diretta.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - dipende dall'ambiente
        raise FetchError(
            "Backend browser non disponibile: installa con "
            "`pip install playwright && playwright install chromium`."
        ) from exc

    captured: list[dict[str, Any]] = []
    url = page_url(cfg.search)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            locale=f"{cfg.search.language}-{cfg.search.market}",
            timezone_id="Europe/Rome",
            user_agent=cfg.fetch.user_agent,
            viewport={"width": 1440, "height": 900},
        )
        page = context.new_page()

        def on_response(response) -> None:
            if "inventory-results" not in response.url:
                return
            try:
                captured.append(response.json())
            except Exception:  # risposta non JSON: la ignoriamo
                pass

        page.on("response", on_response)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=cfg.fetch.timeout * 1000)
            # Scorre la pagina per far caricare le pagine successive di risultati.
            deadline = time.time() + cfg.fetch.browser_wait_ms / 1000
            while time.time() < deadline:
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(1000)
                if captured and time.time() > deadline - cfg.fetch.browser_wait_ms / 2000:
                    break
        finally:
            browser.close()

    if not captured:
        raise BotChallengeError(
            "Il browser non ha ricevuto risultati dall'API Tesla: la pagina potrebbe "
            "aver mostrato una verifica anti-bot. Riprova da un'altra rete."
        )

    merged: list[dict[str, Any]] = []
    total = 0
    for payload in captured:
        merged.extend(extract_results(payload))
        total = max(total, _as_int(payload.get("total_matches_found")))
    return {"results": merged, "total_matches_found": total}


# --- utilita condivise -------------------------------------------------------

def _as_int(value: Any) -> int:
    try:
        return int(str(value).replace(".", "").replace(",", ""))
    except (TypeError, ValueError):
        return 0


def extract_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Estrae la lista di veicoli dalla risposta.

    L'inventario usato restituisce ``results`` come lista; quello nuovo come
    dizionario con chiavi ``exact``/``approximate``. Gestiamo entrambe le forme.
    """
    results = payload.get("results")
    if isinstance(results, list):
        return [r for r in results if isinstance(r, dict)]
    if isinstance(results, dict):
        collected: list[dict[str, Any]] = []
        for key in ("exact", "approximate"):
            value = results.get(key)
            if isinstance(value, list):
                collected.extend(r for r in value if isinstance(r, dict))
        return collected
    return []


def fetch_inventory(cfg: Config) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Scarica l'inventario completo, paginando se necessario.

    Restituisce (veicoli_grezzi, metadati).
    """
    backend = cfg.fetch.backend

    if backend == "file":
        if not cfg.fetch.source_file:
            raise FetchError("backend=file richiede --source-file")
        payload = json.loads(Path(cfg.fetch.source_file).read_text(encoding="utf-8"))
        results = extract_results(payload)
        return results, {"backend": "file", "total_declared": _as_int(payload.get("total_matches_found"))}

    if backend == "browser":
        payload = _fetch_browser(cfg)
        results = extract_results(payload)
        return _dedupe(results), {"backend": "browser",
                                  "total_declared": _as_int(payload.get("total_matches_found"))}

    if backend != "http":
        raise FetchError(f"Backend sconosciuto: {backend}")

    collected: list[dict[str, Any]] = []
    total_declared = 0
    offset = 0
    raw_pages: list[dict[str, Any]] = []

    for _ in range(cfg.search.max_pages):
        payload = _fetch_page_http(cfg, offset)
        raw_pages.append(payload)
        total_declared = max(total_declared, _as_int(payload.get("total_matches_found")))
        page_results = extract_results(payload)
        collected.extend(page_results)
        if len(page_results) < cfg.search.count:
            break
        offset += cfg.search.count
        if total_declared and offset >= total_declared:
            break
        time.sleep(0.8)   # cortesia verso i server Tesla

    meta: dict[str, Any] = {"backend": "http", "total_declared": total_declared}
    if cfg.output.save_raw:
        meta["raw_pages"] = raw_pages
    return _dedupe(collected), meta


def _dedupe(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rimuove i doppioni per VIN mantenendo l'ordine di arrivo."""
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in results:
        vin = str(item.get("VIN") or item.get("vin") or "")
        key = vin or json.dumps(item, sort_keys=True)[:200]
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique
