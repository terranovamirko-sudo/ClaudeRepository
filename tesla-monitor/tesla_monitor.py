#!/usr/bin/env python3
"""Tesla used-inventory monitor.

Checks the Tesla Italy used inventory for Model 3 Rear-Wheel Drive 2023
(white or black) and sends a Telegram and/or ntfy message whenever a car
that was not seen before shows up.

Usage:
    python tesla_monitor.py                 # single check (for cron / GitHub Actions)
    python tesla_monitor.py --loop          # keeps running, checks every hour
    python tesla_monitor.py --test-notify   # sends a test message
    python tesla_monitor.py --get-chat-id   # prints your Telegram chat id

Only the Python standard library is required. If Tesla blocks direct API
requests (HTTP 403), the script falls back to a real browser via Playwright
when it is installed (pip install playwright && playwright install chromium).
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# ---------- Search settings ----------
# Mirrors https://www.tesla.com/it_IT/inventory/used/m3?arrangeby=plh&zip=90146&range=0&PAINT=WHITE,BLACK
MODEL = "m3"
CONDITION = "used"
MARKET = "IT"
LANGUAGE = "it"
ZIP = "90146"
LAT, LNG = 38.1405, 13.3441  # Palermo 90146, only used for distance sorting
RANGE_KM = 0  # 0 = whole country
PAINTS = ["WHITE", "BLACK"]
PAINT_NAMES = {"WHITE": "Bianco", "BLACK": "Nero"}

# Extra filters applied on each result: Model 3 Rear-Wheel Drive, year 2023.
YEARS = {2023}
TRIM_CODES = {"M3RWD"}
TRIM_NAME_KEYWORDS = ("trazione posteriore", "rear-wheel drive")

PAGE_URL = (
    f"https://www.tesla.com/{LANGUAGE}_{MARKET}/inventory/{CONDITION}/{MODEL}"
    f"?arrangeby=plh&zip={ZIP}&range={RANGE_KM}&PAINT={','.join(PAINTS)}"
)
API_URL = "https://www.tesla.com/inventory/api/v4/inventory-results"
PAGE_SIZE = 50
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

HERE = Path(__file__).resolve().parent
DEFAULT_STATE = HERE / "state.json"
CONFIG_FILE = HERE / "config.json"

# Warn once on Telegram/ntfy after this many consecutive failed checks.
FAILURE_ALERT_THRESHOLD = 3


class BlockedError(Exception):
    """Tesla refused the request (typically Akamai bot protection)."""


# ---------- Tesla inventory ----------

def build_query(offset):
    query = {
        "query": {
            "model": MODEL,
            "condition": CONDITION,
            "options": {"PAINT": PAINTS},
            "arrangeby": "Price",
            "order": "asc",
            "market": MARKET,
            "language": LANGUAGE,
            "super_region": "europe",
            "lng": LNG,
            "lat": LAT,
            "zip": ZIP,
            "range": RANGE_KM,
        },
        "offset": offset,
        "count": PAGE_SIZE,
        "outsideOffset": 0,
        "outsideSearch": False,
    }
    return API_URL + "?" + urllib.parse.urlencode({"query": json.dumps(query, separators=(",", ":"))})


def parse_page(payload):
    """Returns (cars, total) from one API response."""
    results = payload.get("results") or []
    # With no exact matches the API may return an object instead of a list.
    if isinstance(results, dict):
        results = results.get("exact") or []
    try:
        total = int(payload.get("total_matches_found") or 0)
    except (TypeError, ValueError):
        total = len(results)
    return results, total


def fetch_all(get_json):
    """Walks every result page using get_json(url) -> dict."""
    cars, offset = [], 0
    while True:
        page, total = parse_page(get_json(build_query(offset)))
        cars.extend(page)
        offset += PAGE_SIZE
        if not page or offset >= total:
            return cars
        time.sleep(1)


def http_get_json(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
        "Referer": PAGE_URL,
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise BlockedError(f"HTTP {e.code}") from e
        raise
    except json.JSONDecodeError as e:
        raise BlockedError("risposta non JSON (probabile pagina di blocco)") from e


def fetch_with_browser():
    """Opens the inventory page in Chromium and calls the API from inside it."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chromium", headless=True)
        except Exception:
            browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_context(locale="it-IT", user_agent=USER_AGENT).new_page()
            page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)

            def get_json(url):
                res = page.evaluate(
                    """async (url) => {
                        const r = await fetch(url, {headers: {Accept: 'application/json'}});
                        return {status: r.status, text: await r.text()};
                    }""",
                    url,
                )
                if res["status"] != 200:
                    raise BlockedError(f"HTTP {res['status']} (browser)")
                return json.loads(res["text"])

            return fetch_all(get_json)
        finally:
            browser.close()


def fetch_inventory():
    try:
        return fetch_all(http_get_json)
    except BlockedError as e:
        print(f"Richiesta diretta bloccata ({e}), provo con il browser...")
    try:
        return fetch_with_browser()
    except ImportError:
        raise BlockedError(
            "Tesla ha bloccato la richiesta e Playwright non è installato "
            "(pip install playwright && playwright install chromium)"
        )


# ---------- Filtering / formatting ----------

def as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def matches(car):
    try:
        if int(car.get("Year")) not in YEARS:
            return False
    except (TypeError, ValueError):
        return False
    paints = {str(p).upper() for p in as_list(car.get("PAINT"))}
    if paints and not paints & set(PAINTS):
        return False
    trims = {str(t).upper() for t in as_list(car.get("TRIM"))}
    name = str(car.get("TrimName") or "").lower()
    return bool(trims & TRIM_CODES) or any(k in name for k in TRIM_NAME_KEYWORDS)


def first(car, *keys):
    for k in keys:
        v = car.get(k)
        if v not in (None, "", []):
            return v[0] if isinstance(v, list) else v
    return None


def fmt_number(n):
    try:
        return f"{int(float(n)):,}".replace(",", ".")
    except (TypeError, ValueError):
        return str(n)


def car_url(vin):
    return f"https://www.tesla.com/{LANGUAGE}_{MARKET}/{MODEL}/order/{vin}?titleStatus={CONDITION}#overview"


def summarize(car):
    vin = car.get("VIN")
    price = first(car, "InventoryPrice", "Price", "PurchasePrice", "TotalPrice")
    km = first(car, "Odometer")
    return {
        "vin": vin,
        "name": first(car, "TrimName") or "Model 3",
        "year": car.get("Year"),
        "paint": first(car, "PAINT"),
        "price": price,
        "km": km,
        "km_unit": first(car, "OdometerTypeShort") or "km",
        "location": first(car, "City", "MetroName", "VrlName", "StateProvince"),
        "url": car_url(vin),
    }


def format_car(s):
    paint = PAINT_NAMES.get(str(s["paint"]).upper(), s["paint"] or "")
    lines = [f"🚗 {s['name']} {s['year']} – {paint}"]
    if s["price"] is not None:
        lines.append(f"💶 € {fmt_number(s['price'])}")
    if s["km"] is not None:
        lines.append(f"📏 {fmt_number(s['km'])} {s['km_unit']}")
    if s["location"]:
        lines.append(f"📍 {s['location']}")
    lines.append(s["url"])
    return "\n".join(lines)


# ---------- Notifications ----------

def load_config():
    cfg = {}
    if CONFIG_FILE.exists():
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    for key in ("telegram_bot_token", "telegram_chat_id", "ntfy_topic", "ntfy_server"):
        env = os.environ.get(key.upper())
        if env:
            cfg[key] = env
    return cfg


def post(url, data, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def send_telegram(cfg, text):
    url = f"https://api.telegram.org/bot{cfg['telegram_bot_token']}/sendMessage"
    body = urllib.parse.urlencode({
        "chat_id": cfg["telegram_chat_id"],
        "text": text,
        "disable_web_page_preview": "true",
    }).encode()
    post(url, body)


def send_ntfy(cfg, text):
    server = cfg.get("ntfy_server") or "https://ntfy.sh"
    post(f"{server.rstrip('/')}/{cfg['ntfy_topic']}", text.encode("utf-8"),
         {"Title": "Tesla Model 3 usate", "Tags": "car"})


def channels(cfg):
    found = []
    if cfg.get("telegram_bot_token") and cfg.get("telegram_chat_id"):
        found.append(("Telegram", send_telegram))
    if cfg.get("ntfy_topic"):
        found.append(("ntfy", send_ntfy))
    return found


def notify(cfg, text):
    """Sends text on every configured channel. Returns True if it was delivered."""
    if not channels(cfg):
        print("Nessun canale di notifica configurato, stampo il messaggio:\n" + text)
        return True
    ok = False
    for name, send in channels(cfg):
        try:
            send(cfg, text)
            ok = True
        except Exception as e:
            print(f"Invio {name} fallito: {e}", file=sys.stderr)
    return ok


def chunks(blocks, limit=3800):
    """Joins text blocks into messages under Telegram's 4096-char limit."""
    msg = ""
    for b in blocks:
        if msg and len(msg) + len(b) + 2 > limit:
            yield msg
            msg = ""
        msg = f"{msg}\n\n{b}" if msg else b
    if msg:
        yield msg


# ---------- State ----------

def load_state(path):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def save_state(path, state):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ---------- Main check ----------

def check(state_path, cfg):
    """Runs one check. Returns True on success."""
    now = datetime.now().isoformat(timespec="seconds")
    state = load_state(state_path) or {"seen": {}, "failures": 0, "initialized": False}
    first_run = not state.get("initialized")

    try:
        cars = [summarize(c) for c in fetch_inventory() if matches(c) and c.get("VIN")]
    except Exception as e:
        state["failures"] = state.get("failures", 0) + 1
        print(f"[{now}] Controllo fallito ({state['failures']} di fila): {e}", file=sys.stderr)
        if state["failures"] == FAILURE_ALERT_THRESHOLD:
            notify(cfg, f"⚠️ Il monitor Tesla non riesce a leggere l'inventario da "
                        f"{state['failures']} controlli consecutivi.\nUltimo errore: {e}")
        save_state(state_path, state)
        return False

    state["failures"] = 0
    new = [c for c in cars if c["vin"] not in state["seen"]]
    print(f"[{now}] {len(cars)} auto corrispondenti, {len(new)} nuove")

    if first_run:
        header = (f"✅ Monitor Tesla attivo: Model 3 Trazione Posteriore 2023 usate "
                  f"(bianche/nere). Attualmente disponibili: {len(cars)}.\n"
                  f"Ti avviserò quando ne arriva una nuova.\n{PAGE_URL}")
        blocks = [header] + [format_car(c) for c in cars]
    elif new:
        header = ("🆕 Nuova Model 3 Trazione Posteriore 2023 disponibile!" if len(new) == 1
                  else f"🆕 {len(new)} nuove Model 3 Trazione Posteriore 2023 disponibili!")
        blocks = [header] + [format_car(c) for c in new]
    else:
        blocks = []

    sent = all(notify(cfg, m) for m in chunks(blocks)) if blocks else True
    # If the notification failed, don't mark the new cars as seen so they are retried.
    if sent or first_run:
        for c in new:
            state["seen"][c["vin"]] = {**c, "first_seen": now}
    state["initialized"] = True
    state["last_check"] = now
    save_state(state_path, state)
    return True


def get_chat_id(cfg):
    token = cfg.get("telegram_bot_token")
    if not token:
        sys.exit("Imposta prima TELEGRAM_BOT_TOKEN (o telegram_bot_token in config.json).")
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30) as r:
        updates = json.loads(r.read()).get("result", [])
    chats = {u["message"]["chat"]["id"]: u["message"]["chat"] for u in updates if "message" in u}
    if not chats:
        sys.exit("Nessun messaggio trovato: scrivi un messaggio qualsiasi al tuo bot e riprova.")
    for cid, chat in chats.items():
        print(f"chat_id: {cid}  ({chat.get('first_name') or chat.get('title') or ''})")


def main():
    ap = argparse.ArgumentParser(description="Monitor inventario Tesla Model 3 usate.")
    ap.add_argument("--loop", action="store_true", help="resta attivo e controlla periodicamente")
    ap.add_argument("--interval", type=int, default=60, help="minuti tra i controlli con --loop (default 60)")
    ap.add_argument("--state", type=Path, default=DEFAULT_STATE, help="file dove salvare le auto già viste")
    ap.add_argument("--test-notify", action="store_true", help="invia un messaggio di prova ed esce")
    ap.add_argument("--get-chat-id", action="store_true", help="mostra il chat_id Telegram ed esce")
    args = ap.parse_args()

    cfg = load_config()
    if args.get_chat_id:
        return get_chat_id(cfg)
    if args.test_notify:
        if not channels(cfg):
            sys.exit("Nessun canale configurato: imposta Telegram e/o ntfy (vedi README).")
        ok = notify(cfg, "🔔 Test dal monitor Tesla: le notifiche funzionano!")
        sys.exit(0 if ok else 1)

    if not args.loop:
        sys.exit(0 if check(args.state, cfg) else 1)
    while True:
        check(args.state, cfg)
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
