"""Interfaccia a riga di comando dell'analizzatore.

Esempi:
    python3 -m tesla_inventory                      # una analisi adesso
    python3 -m tesla_inventory --loop               # controllo ogni 3 ore
    python3 -m tesla_inventory --max-price 30000    # solo sotto i 30.000 euro
    python3 -m tesla_inventory --cron-line          # riga di crontab pronta
"""

from __future__ import annotations

import argparse
import json
import random
import signal
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from . import alert_report, mailer, report
from .alerts import OpportunityArchive, describe_thresholds
from .app import RunResult, run_once
from .config import Config
from .fetch import BotChallengeError, FetchError

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_BLOCKED = 2

_stop = False


def _handle_signal(signum, frame) -> None:   # pragma: no cover - dipende dal runtime
    global _stop
    _stop = True
    print("\nInterruzione richiesta: esco dopo il ciclo corrente.", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesla_inventory",
        description="Analizza l'inventario Tesla usato e riepiloga il miglior "
                    "rapporto qualita/prezzo.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    ricerca = parser.add_argument_group("ricerca")
    ricerca.add_argument("--config", help="file JSON di configurazione")
    ricerca.add_argument("--model", help="m3 (Model 3), my, ms, mx")
    ricerca.add_argument("--zip", dest="zip_code", help="CAP di riferimento")
    ricerca.add_argument("--market", help="codice mercato, es. IT")
    ricerca.add_argument("--language", help="lingua, es. it")
    ricerca.add_argument("--condition", choices=["used", "new"], help="usato o nuovo")

    filtri = parser.add_argument_group("filtri")
    filtri.add_argument("--max-price", type=float, help="prezzo massimo in euro")
    filtri.add_argument("--min-price", type=float, help="prezzo minimo in euro")
    filtri.add_argument("--max-km", type=int, dest="max_odometer", help="chilometri massimi")
    filtri.add_argument("--min-year", type=int, help="anno minimo")
    filtri.add_argument("--max-year", type=int, help="anno massimo")
    filtri.add_argument("--trim", action="append", choices=["RWD", "LR", "PERF"],
                        help="allestimento da includere (ripetibile)")
    filtri.add_argument("--color", action="append", dest="colors",
                        choices=["black", "white", "blue", "red", "grey", "silver"],
                        help="colore da includere (ripetibile)")
    filtri.add_argument("--no-damaged", action="store_true",
                        help="escludi le auto con danni dichiarati")

    analisi = parser.add_argument_group("analisi")
    analisi.add_argument("--rank", choices=["score", "deal", "price"], default="score",
                         help="criterio di ordinamento (default: score)")
    analisi.add_argument("--top", type=int, help="quante auto nel riepilogo dettagliato")
    analisi.add_argument("--rows", type=int, help="quante righe nella classifica")

    occasioni = parser.add_argument_group("occasioni e avvisi")
    occasioni.add_argument("--alert", action="store_true",
                           help="modalita sorveglianza: segnala solo le vere occasioni")
    occasioni.add_argument("--min-score", type=float,
                           help="punteggio minimo perche sia un'occasione")
    occasioni.add_argument("--min-advantage", type=float, metavar="PCT",
                           help="convenienza minima in percentuale")
    occasioni.add_argument("--budget", type=float,
                           help="tetto di spesa oltre il quale non e un'occasione")
    occasioni.add_argument("--test-email", action="store_true",
                           help="invia una email di prova ed esci")
    occasioni.add_argument("--archivio", action="store_true",
                           help="mostra le occasioni gia archiviate ed esci")

    rete = parser.add_argument_group("rete")
    rete.add_argument("--backend", choices=["http", "browser", "file"],
                      help="come leggere i dati (default: http)")
    rete.add_argument("--source-file", help="JSON gia salvato, con --backend file")
    rete.add_argument("--timeout", type=int, help="timeout in secondi")
    rete.add_argument("--save-raw", action="store_true",
                      help="salva la risposta grezza dell'API")

    esecuzione = parser.add_argument_group("esecuzione")
    esecuzione.add_argument("--loop", action="store_true",
                            help="ripeti l'analisi a intervalli regolari")
    esecuzione.add_argument("--every", type=float, metavar="ORE",
                            help="intervallo in ore per --loop (default: 3)")
    esecuzione.add_argument("--data-dir", help="cartella per storico e report")
    esecuzione.add_argument("--dry-run", action="store_true",
                            help="non scrivere nulla su disco e non notificare")

    output = parser.add_argument_group("output")
    output.add_argument("--json", action="store_true", dest="as_json",
                        help="stampa il risultato in JSON invece del riepilogo")
    output.add_argument("--quiet", action="store_true", help="stampa solo gli errori")
    output.add_argument("--no-color", action="store_true", help="disattiva i colori ANSI")

    utilita = parser.add_argument_group("utilita")
    utilita.add_argument("--print-config", action="store_true",
                         help="stampa la configurazione risultante ed esci")
    utilita.add_argument("--cron-line", action="store_true",
                         help="stampa una riga di crontab pronta da incollare")
    return parser


def apply_cli(cfg: Config, args: argparse.Namespace) -> Config:
    """Sovrascrive la configurazione con le opzioni da riga di comando."""
    for attr, target in (("model", "model"), ("zip_code", "zip_code"), ("market", "market"),
                         ("language", "language"), ("condition", "condition")):
        value = getattr(args, attr, None)
        if value is not None:
            setattr(cfg.search, target, value)

    for attr in ("max_price", "min_price", "max_odometer", "min_year", "max_year"):
        value = getattr(args, attr, None)
        if value is not None:
            setattr(cfg.filters, attr, value)
    if args.trim:
        cfg.filters.trims = args.trim
    if args.colors:
        cfg.filters.colors = args.colors
    if args.no_damaged:
        cfg.filters.exclude_damaged = True

    if args.alert:
        cfg.alert.enabled = True
    if args.min_score is not None:
        cfg.alert.min_score = args.min_score
    if args.min_advantage is not None:
        cfg.alert.min_advantage_pct = args.min_advantage
    if args.budget is not None:
        cfg.alert.max_price = args.budget

    if args.backend:
        cfg.fetch.backend = args.backend
    if args.source_file:
        cfg.fetch.source_file = args.source_file
        cfg.fetch.backend = "file"
    if args.timeout:
        cfg.fetch.timeout = args.timeout
    if args.save_raw:
        cfg.output.save_raw = True

    if args.data_dir:
        cfg.output.data_dir = args.data_dir
    if args.top:
        cfg.output.top_n = args.top
    if args.rows:
        cfg.output.table_rows = args.rows
    if args.no_color or not sys.stdout.isatty():
        cfg.output.color = False
    if args.every:
        cfg.interval_hours = args.every
    return cfg


def result_to_json(cfg: Config, result: RunResult) -> str:
    """Serializza l'esito per chi vuole incatenare altri strumenti."""
    def entry(valuation) -> dict:
        listing = valuation.listing
        return {
            "vin": listing.vin,
            "etichetta": listing.label,
            "anno": listing.year,
            "allestimento": listing.trim,
            "km": listing.odometer_km,
            "prezzo": valuation.price,
            "valore_stimato": round(valuation.estimated_value, 2),
            "convenienza_eur": round(valuation.advantage_eur, 2),
            "convenienza_pct": round(valuation.advantage_pct, 2),
            "punteggio": valuation.score,
            "dettaglio_punteggio": valuation.score_parts,
            "autonomia_km": listing.range_km,
            "url": listing.url,
            "note": valuation.notes,
        }

    payload = {
        "aggiornato": datetime.now().isoformat(timespec="seconds"),
        "ricerca": report.search_description(cfg),
        "totale_trovate": result.total_found,
        "escluse_dai_filtri": result.filtered_out,
        "classifica": [entry(v) for v in result.ranked],
        "occasioni": {
            "sorveglianza_attiva": cfg.alert.enabled,
            "criteri": describe_thresholds(cfg.alert),
            "trovate": [entry(v) for v in result.qualifying],
            "da_segnalare": [entry(o.valuation) for o in result.opportunities],
            "email_inviata": result.email_sent,
        },
        "novita": {
            "primo_giro": result.changes.first_run,
            "nuovi": [entry(v) for v in result.changes.new_listings],
            "ribassi": [{**entry(c.valuation), "prezzo_precedente": c.old_price,
                         "variazione_pct": round(c.delta_pct, 2)}
                        for c in result.changes.price_drops],
            "rincari": [{**entry(c.valuation), "prezzo_precedente": c.old_price,
                         "variazione_pct": round(c.delta_pct, 2)}
                        for c in result.changes.price_rises],
            "sparite": result.changes.removed,
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _print_result(cfg: Config, args: argparse.Namespace, result: RunResult) -> None:
    if args.as_json:
        print(result_to_json(cfg, result))
        return
    if cfg.alert.enabled:
        print()
        print(alert_report.format_console_verdict(cfg, result, color=cfg.output.color))
        print()
        if args.quiet:
            return
    if not args.quiet:
        print(report.format_console(cfg, result.ranked, result.skipped,
                                    result.changes, result.meta))
        for path in result.written_files:
            print(f"  Report salvato: {path}")
        for problem in result.notify_problems:
            print(f"  Notifica non inviata — {problem}", file=sys.stderr)
        if result.written_files:
            print()


def _cron_line(cfg: Config) -> str:
    script = Path(__file__).resolve().parent.parent
    hours = max(1, int(round(cfg.interval_hours)))
    return (f"0 */{hours} * * * cd {script} && "
            f"{sys.executable} -m tesla_inventory --quiet >> {script}/data/cron.log 2>&1")


def _test_email(cfg: Config) -> int:
    """Verifica la configurazione email senza aspettare un'occasione."""
    problems = mailer.check_config(cfg.email)
    if problems:
        print("Configurazione email incompleta:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return EXIT_ERROR
    for note in mailer.warnings(cfg.email):
        print(f"Attenzione: {note}\n", file=sys.stderr)
    try:
        mailer.send_test(cfg.email)
    except mailer.EmailError as exc:
        print(f"Invio non riuscito: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(f"Email di prova inviata a {', '.join(cfg.email.recipients)}.")
    print("Se non arriva entro qualche minuto, controlla la posta indesiderata.")
    return EXIT_OK


def _show_archive(cfg: Config) -> int:
    """Stampa le occasioni archiviate, dalla piu recente."""
    archive = OpportunityArchive(cfg.output.data_dir)
    entries = archive.load().get("occasioni", {})
    if not entries:
        print("Nessuna occasione archiviata finora.")
        print(f"Criteri attuali: {describe_thresholds(cfg.alert)}")
        return EXIT_OK

    ordered = sorted(entries.items(), key=lambda kv: kv[1].get("ultima_volta", ""),
                     reverse=True)
    print(report.plural(len(ordered), "occasione archiviata",
                        "occasioni archiviate") + "\n")
    for vin, data in ordered:
        segnalata = data.get("notificata_il", "mai")
        print(f"  {data.get('etichetta', vin)} \u2014 {data.get('colore', 'n.d.')}")
        print(f"     {report.eur(data.get('prezzo_attuale'))} \u00b7 "
              f"{report.km(data.get('km'))} \u00b7 "
              f"punteggio {report.num(data.get('punteggio'))} \u00b7 "
              f"convenienza {report.pct(data.get('convenienza_pct'))}")
        print(f"     vista la prima volta il {data.get('prima_volta', 'n.d.')[:10]} \u00b7 "
              f"segnalata: {segnalata[:10] if segnalata != 'mai' else 'mai'}")
        print(f"     VIN {vin}")
        print(f"     {data.get('url', '')}")
        print()
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        cfg = apply_cli(Config.load(args.config), args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Configurazione non valida: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.print_config:
        print(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2))
        return EXIT_OK
    if args.cron_line:
        print(_cron_line(cfg))
        return EXIT_OK
    if args.test_email:
        return _test_email(cfg)
    if args.archivio:
        return _show_archive(cfg)

    if not args.loop:
        return _run_and_report(cfg, args)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    interval = max(0.05, cfg.interval_hours) * 3600
    consecutive_failures = 0

    while not _stop:
        code = _run_and_report(cfg, args)
        consecutive_failures = consecutive_failures + 1 if code == EXIT_ERROR else 0

        # In caso di errori ripetuti allunga l'attesa, per non insistere a vuoto.
        wait = interval * min(4, 2 ** consecutive_failures) if consecutive_failures else interval
        wait += random.uniform(0, min(300, wait * 0.05))   # jitter anti-sincronizzazione
        if _stop:
            break
        resume = datetime.now() + timedelta(seconds=wait)
        if not args.quiet:
            print(f"  Prossimo controllo alle {resume.strftime('%H:%M del %d/%m/%Y')}.\n")
        # Attesa a piccoli passi, cosi Ctrl+C risponde subito.
        deadline = time.monotonic() + wait
        while not _stop and time.monotonic() < deadline:
            time.sleep(min(5.0, deadline - time.monotonic()))
    return EXIT_OK


def _run_and_report(cfg: Config, args: argparse.Namespace) -> int:
    try:
        result = run_once(cfg, rank_mode=args.rank, dry_run=args.dry_run)
    except BotChallengeError as exc:
        print(f"Accesso bloccato da Tesla: {exc}", file=sys.stderr)
        return EXIT_BLOCKED
    except FetchError as exc:
        print(f"Download non riuscito: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:                      # pragma: no cover - rete imprevedibile
        print(f"Errore inatteso: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return EXIT_ERROR

    _print_result(cfg, args, result)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
