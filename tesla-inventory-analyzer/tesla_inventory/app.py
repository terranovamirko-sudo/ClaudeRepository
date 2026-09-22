"""Orchestrazione di una singola analisi: scarica, valuta, confronta, riporta."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import html_report, notify, report
from .config import Config
from .fetch import fetch_inventory
from .history import Changes, HistoryStore
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


def apply_filters(cfg: Config, listings: list[Listing]) -> list[Listing]:
    """Applica i filtri configurati (prezzo, km, anno, allestimento, danni)."""
    f = cfg.filters
    kept: list[Listing] = []
    for listing in listings:
        price = listing.effective_price
        if f.max_price is not None and (price is None or price > f.max_price):
            continue
        if f.min_price is not None and (price is None or price < f.min_price):
            continue
        if f.max_odometer is not None and (listing.odometer_km is None
                                           or listing.odometer_km > f.max_odometer):
            continue
        if f.min_year is not None and (listing.year is None or listing.year < f.min_year):
            continue
        if f.max_year is not None and (listing.year is None or listing.year > f.max_year):
            continue
        if f.trims and listing.trim not in f.trims:
            continue
        if f.exclude_damaged and listing.has_damage:
            continue
        kept.append(listing)
    return kept


def run_once(cfg: Config, *, rank_mode: str = "score", dry_run: bool = False) -> RunResult:
    """Esegue un'analisi completa e scrive i report richiesti."""
    result = RunResult()

    records, meta = fetch_inventory(cfg)
    result.meta = meta
    result.total_found = len(records)

    listings = parse_all(records, market=cfg.search.market, language=cfg.search.language,
                         zip_code=cfg.search.zip_code)
    filtered = apply_filters(cfg, listings)
    result.filtered_out = len(listings) - len(filtered)

    valuations, calibration = evaluate_all(cfg, filtered)
    meta["calibration"] = round(calibration, 4)
    result.ranked = rank(valuations, rank_mode)
    result.skipped = [v for v in valuations if not v.valid]

    data_dir = Path(cfg.output.data_dir)
    store = HistoryStore(data_dir)
    state = store.load()
    result.changes = store.diff(state, result.ranked)

    if not dry_run:
        store.save(store.update(state, result.ranked))
        store.append_run(result.ranked, result.changes, meta)
        result.written_files = _write_reports(cfg, result, data_dir)
        result.notify_problems = notify.notify(
            cfg.notify,
            report.format_notification(cfg, result.ranked, result.changes),
            has_news=result.changes.has_news or result.changes.first_run,
        )
    return result


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
