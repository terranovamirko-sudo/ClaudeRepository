"""Riconoscimento e archiviazione delle occasioni di acquisto.

Distingue "la migliore fra quelle in vendita" da "una che vale davvero la pena
comprare". Senza questa distinzione un controllo ogni 3 ore segnalerebbe
qualcosa a ogni giro, e smetteresti di leggerlo dopo due giorni.

L'archivio serve a due cose: tenere memoria delle occasioni trovate anche dopo
che l'auto e stata venduta, e non ripetere lo stesso avviso otto volte al giorno.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import AlertConfig, Config
from .parse import COLOR_LABELS
from .report import eur, num
from .valuation import Valuation

ARCHIVE_VERSION = 1
MAX_HISTORY_POINTS = 40


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Opportunity:
    """Un'auto che supera le soglie, con il motivo per cui le supera."""

    valuation: Valuation
    reasons: list[str] = field(default_factory=list)
    first_time: bool = True
    previous_notified_price: float | None = None

    @property
    def drop_since_notified(self) -> float | None:
        if self.previous_notified_price is None:
            return None
        return self.valuation.price - self.previous_notified_price


def describe_thresholds(cfg: AlertConfig) -> str:
    """Le soglie in parole, per spiegare all'utente cosa sta cercando il programma."""
    parts = [f"punteggio ≥ {num(cfg.min_score, 0)}",
             f"convenienza ≥ {num(cfg.min_advantage_pct, 0)}%"]
    if cfg.min_advantage_eur:
        parts.append(f"almeno {eur(cfg.min_advantage_eur)} sotto il valore stimato")
    if cfg.max_price:
        parts.append(f"prezzo ≤ {eur(cfg.max_price)}")
    return " · ".join(parts)


def evaluate_thresholds(cfg: AlertConfig, valuation: Valuation) -> tuple[bool, list[str]]:
    """Verifica le soglie su una singola auto.

    Ritorna (supera, motivi). I motivi descrivono il superamento quando l'esito
    e positivo, e cosa e mancato quando e negativo: servono a rendere leggibile
    sia l'avviso sia lo scarto.
    """
    if not valuation.valid:
        return False, ["valutazione non disponibile"]

    passed: list[str] = []
    failed: list[str] = []

    if valuation.score >= cfg.min_score:
        passed.append(f"punteggio {num(valuation.score)}/100")
    else:
        failed.append(f"punteggio {num(valuation.score)} (soglia {num(cfg.min_score, 0)})")

    if valuation.advantage_pct >= cfg.min_advantage_pct:
        passed.append(f"costa il {num(valuation.advantage_pct)}% meno del valore stimato")
    else:
        failed.append(f"convenienza {num(valuation.advantage_pct)}% "
                      f"(soglia {num(cfg.min_advantage_pct, 0)}%)")

    if cfg.min_advantage_eur:
        if valuation.advantage_eur >= cfg.min_advantage_eur:
            passed.append(f"{eur(valuation.advantage_eur)} sotto il valore stimato")
        else:
            failed.append(f"solo {eur(valuation.advantage_eur)} sotto il valore stimato, "
                          f"servono {eur(cfg.min_advantage_eur)}")

    if cfg.max_price is not None:
        if valuation.price <= cfg.max_price:
            passed.append(f"entro il budget di {eur(cfg.max_price)}")
        else:
            failed.append(f"prezzo oltre il budget di {eur(cfg.max_price)}")

    return (not failed), (passed if not failed else failed)


class OpportunityArchive:
    """Memoria delle occasioni trovate, su file JSON."""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "occasioni.json"

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": ARCHIVE_VERSION, "occasioni": {}}
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Un archivio illeggibile non deve fermare la sorveglianza.
            return {"version": ARCHIVE_VERSION, "occasioni": {}, "corrotto": True}
        state.setdefault("occasioni", {})
        state["version"] = ARCHIVE_VERSION
        return state

    def save(self, state: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)          # scrittura atomica

    def find(self, cfg: Config, valuations: list[Valuation]) -> list[Valuation]:
        """Le auto che superano le soglie, dalla piu conveniente."""
        qualifying = [v for v in valuations if evaluate_thresholds(cfg.alert, v)[0]]
        return sorted(qualifying, key=lambda v: (-v.score, -v.advantage_pct))

    def to_notify(self, cfg: Config, state: dict[str, Any],
                  qualifying: list[Valuation]) -> list[Opportunity]:
        """Filtra le occasioni gia segnalate.

        Un'auto torna a essere una notizia solo se il prezzo e sceso ancora di
        almeno ``repeat_after_drop_eur`` dall'ultimo avviso: altrimenti la stessa
        auto arriverebbe per email otto volte al giorno.
        """
        stored = state.get("occasioni", {})
        pending: list[Opportunity] = []

        for valuation in qualifying:
            _, reasons = evaluate_thresholds(cfg.alert, valuation)
            previous = stored.get(valuation.listing.vin)
            if previous is None:
                pending.append(Opportunity(valuation, reasons, first_time=True))
                continue

            notified_price = previous.get("prezzo_notificato")
            if notified_price is None:
                pending.append(Opportunity(valuation, reasons, first_time=False))
                continue
            if valuation.price <= notified_price - cfg.alert.repeat_after_drop_eur:
                pending.append(Opportunity(valuation, reasons, first_time=False,
                                           previous_notified_price=float(notified_price)))
        return pending

    def record(self, state: dict[str, Any], qualifying: list[Valuation],
               notified: list[Opportunity]) -> dict[str, Any]:
        """Archivia le occasioni trovate e segna quali sono state comunicate."""
        timestamp = _now()
        stored = state.setdefault("occasioni", {})
        notified_vins = {o.valuation.listing.vin for o in notified}

        for valuation in qualifying:
            listing = valuation.listing
            entry = stored.get(listing.vin, {})
            history = list(entry.get("storico", []))
            point = [timestamp, valuation.price, valuation.score]
            if not history or history[-1][1:] != point[1:]:
                history.append(point)

            entry.update({
                "prima_volta": entry.get("prima_volta", timestamp),
                "ultima_volta": timestamp,
                "etichetta": listing.label,
                "anno": listing.year,
                "colore": COLOR_LABELS.get(listing.color, listing.color) or "n.d.",
                "km": listing.odometer_km,
                "prezzo_attuale": valuation.price,
                "valore_stimato": round(valuation.estimated_value),
                "convenienza_pct": round(valuation.advantage_pct, 2),
                "convenienza_eur": round(valuation.advantage_eur),
                "punteggio": valuation.score,
                "url": listing.url,
                "storico": history[-MAX_HISTORY_POINTS:],
            })
            if listing.vin in notified_vins:
                entry["notificata_il"] = timestamp
                entry["prezzo_notificato"] = valuation.price
            stored[listing.vin] = entry

        return state
