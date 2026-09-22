"""Orchestrazione di una singola analisi: scarica, valuta, filtra, avvisa."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import alert_report, html_report, mailer, notify, report
from .alerts import Opportunity, OpportunityArchive
from .config import Config, FilterConfig
from .fetch import fetch_inventory
from .history import Changes, HistoryStore
from .mailer import EmailError
from .parse import Listing, parse_all
from .valuation import Valuation, evaluate_all, rank


@dataclass
class RunResult:
    """Esito di un'esecuzione, pronto per essere stampato o salvato."""

    ranked: list[Valuation] = field(default_factory=list)
    skipped: list[Valuation] = field(default_factory=list)
    changes: Changes = field(default_factory=Changes)
    meta: dict[str, Any] = field(default_factory=dict)
    written_files: list[Path] = field(default_factory=list)
    notify_problems: list[str] = field(default_factory=list)
    total_found: int = 0
    filtered_out: int = 0
    baseline_count: int = 0                              # auto usate come riferimento
    qualifying: list[Valuation] = field(default_factory=list)   # superano le soglie
    opportunities: list[Opportunity] = field(default_factory=list)  # da comunicare
    email_sent: bool = False

    @property
    def alert_active(self) -> bool:
        return bool(self.qualifying)


def matches_filters(f: FilterConfig, listing: Listing) -> bool:
    """Se questa auto rientra in cio che l'utente sta cercando."""
    price = listing.effective_price
    if f.max_price is not None and (price is None or price > f.max_price):
        return False
    if f.min_price is not None and (price is None or price < f.min_price):
        return False
    if f.max_odometer is not None and (listing.odometer_km is None
                                       or listing.odometer_km > f.max_odometer):
        return False
    if f.min_year is not None and (listing.year is None or listing.year < f.min_year):
        return False
    if f.max_year is not None and (listing.year is None or listing.year > f.max_year):
        return False
    if f.trims and listing.trim not in f.trims:
        return False
    if f.colors and listing.color not in f.colors:
        return False
    if f.exclude_damaged and listing.has_damage:
        return False
    return True


def apply_filters(cfg: Config, listings: list[Listing]) -> list[Listing]:
    """Filtra una lista di annunci secondo la configurazione."""
    return [l for l in listings if matches_filters(cfg.filters, l)]


def run_once(cfg: Config, *, rank_mode: str = "score", dry_run: bool = False) -> RunResult:
    """Esegue un'analisi completa e scrive i report richiesti."""
    result = RunResult()

    records, meta = fetch_inventory(cfg)
    result.meta = meta
    result.total_found = len(records)

    listings = parse_all(records, market=cfg.search.market, language=cfg.search.language,
                         zip_code=cfg.search.zip_code)

    # La valutazione parte da TUTTO l'inventario, prima dei filtri: il valore di
    # riferimento nasce dal confronto fra le auto in vendita, e restringendo
    # troppo presto quelle poche auto diventerebbero il metro di se stesse.
    all_valuations, calibration = evaluate_all(cfg, listings)
    meta["calibration"] = round(calibration, 4)
    result.baseline_count = sum(1 for v in all_valuations if v.valid)

    matching = [v for v in all_valuations if matches_filters(cfg.filters, v.listing)]
    result.filtered_out = len(all_valuations) - len(matching)
    result.ranked = rank(matching, rank_mode)
    result.skipped = [v for v in matching if not v.valid]

    data_dir = Path(cfg.output.data_dir)
    store = HistoryStore(data_dir)
    state = store.load()
    result.changes = store.diff(state, result.ranked)

    if cfg.alert.enabled:
        _run_alerts(cfg, result, data_dir, dry_run=dry_run)

    if not dry_run:
        store.save(store.update(state, result.ranked))
        store.append_run(result.ranked, result.changes, meta)
        result.written_files = _write_reports(cfg, result, data_dir)
        result.notify_problems += notify.notify(
            cfg.notify,
            report.format_notification(cfg, result.ranked, result.changes),
            has_news=result.changes.has_news or result.changes.first_run,
        )
    return result


def _run_alerts(cfg: Config, result: RunResult, data_dir: Path, *, dry_run: bool) -> None:
    """Cerca le occasioni, le comunica e le archivia.

    L'ordine conta: l'email parte prima dell'archiviazione, e un'occasione viene
    segnata come comunicata solo se e stata davvero recapitata. Altrimenti un
    errore di invio la seppellirebbe per sempre.
    """
    archive = OpportunityArchive(data_dir)
    state = archive.load()

    result.qualifying = archive.find(cfg, result.ranked)
    result.opportunities = archive.to_notify(cfg, state, result.qualifying)

    if dry_run or not result.opportunities:
        return

    delivered = True
    if cfg.email.enabled:
        try:
            mailer.send(
                cfg.email,
                alert_report.subject(result.opportunities),
                alert_report.text_body(cfg, result.opportunities, result.meta),
                alert_report.html_body(cfg, result.opportunities, result.meta),
            )
            result.email_sent = True
        except EmailError as exc:
            result.notify_problems.append(f"Email non inviata — {exc}")
            delivered = False

    archive.save(archive.record(state, result.qualifying,
                                result.opportunities if delivered else []))


def _write_reports(cfg: Config, result: RunResult, data_dir: Path) -> list[Path]:
    """Salva i report su disco: ultimo aggiornamento + copia storica."""
    written: list[Path] = []
    reports_dir = data_dir / "report"
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")

    if cfg.output.write_markdown:
        text = report.format_markdown(cfg, result.ranked, result.skipped,
                                      result.changes, result.meta)
        for path in (reports_dir / "ultimo.md", reports_dir / f"report-{stamp}.md"):
            path.write_text(text, encoding="utf-8")
            written.append(path)

    if cfg.output.write_html:
        page = html_report.format_html(cfg, result.ranked, result.skipped,
                                       result.changes, result.meta)
        path = reports_dir / "ultimo.html"
        path.write_text(page, encoding="utf-8")
        written.append(path)

    if cfg.output.save_raw and result.meta.get("raw_pages"):
        path = reports_dir / f"grezzo-{stamp}.json"
        path.write_text(json.dumps(result.meta["raw_pages"], ensure_ascii=False),
                        encoding="utf-8")
        written.append(path)
        # La copia grezza non serve piu in memoria.
        result.meta.pop("raw_pages", None)
    return written
