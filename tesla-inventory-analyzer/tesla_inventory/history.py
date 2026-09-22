"""Memoria fra un'esecuzione e l'altra: nuovi annunci, ribassi, auto sparite.

E la parte che da senso a un controllo ogni 3 ore: senza storico ogni giro
riporterebbe le stesse auto, senza dire cosa e cambiato.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .valuation import Valuation

STATE_VERSION = 2
MAX_PRICE_POINTS = 60      # ~7 giorni di storico prezzi a 3 ore di cadenza
MAX_RUN_RECORDS = 500


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class PriceChange:
    valuation: Valuation
    old_price: float
    new_price: float

    @property
    def delta(self) -> float:
        return self.new_price - self.old_price

    @property
    def delta_pct(self) -> float:
        return (self.delta / self.old_price * 100.0) if self.old_price else 0.0


@dataclass
class Changes:
    """Differenze rispetto all'esecuzione precedente."""

    first_run: bool = False
    new_listings: list[Valuation] = field(default_factory=list)
    price_drops: list[PriceChange] = field(default_factory=list)
    price_rises: list[PriceChange] = field(default_factory=list)
    removed: list[dict[str, Any]] = field(default_factory=list)
    still_available: int = 0
    previous_run: str | None = None

    @property
    def has_news(self) -> bool:
        return bool(self.new_listings or self.price_drops or self.price_rises or self.removed)


class HistoryStore:
    """Persistenza su file JSON dello stato dell'inventario osservato."""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.state_path = self.data_dir / "state.json"
        self.runs_path = self.data_dir / "runs.jsonl"

    # -- lettura/scrittura ----------------------------------------------------

    def load(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"version": STATE_VERSION, "last_run": None, "vehicles": {}}
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Uno stato corrotto non deve impedire l'analisi: si riparte da zero.
            return {"version": STATE_VERSION, "last_run": None, "vehicles": {}, "corrupted": True}
        state.setdefault("vehicles", {})
        state.setdefault("last_run", None)
        state["version"] = STATE_VERSION
        return state

    def save(self, state: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)   # scrittura atomica

    # -- confronto ------------------------------------------------------------

    def diff(self, state: dict[str, Any], valuations: list[Valuation]) -> Changes:
        """Confronta la fotografia attuale con quella salvata."""
        stored: dict[str, Any] = state.get("vehicles", {})
        changes = Changes(first_run=not stored, previous_run=state.get("last_run"))
        current_vins = set()

        for valuation in valuations:
            vin = valuation.listing.vin
            current_vins.add(vin)
            previous = stored.get(vin)
            if previous is None:
                if not changes.first_run:
                    changes.new_listings.append(valuation)
                continue

            changes.still_available += 1
            old_price = previous.get("price")
            if isinstance(old_price, (int, float)) and valuation.price:
                if valuation.price < old_price - 0.5:
                    changes.price_drops.append(PriceChange(valuation, float(old_price), valuation.price))
                elif valuation.price > old_price + 0.5:
                    changes.price_rises.append(PriceChange(valuation, float(old_price), valuation.price))

        for vin, previous in stored.items():
            if vin not in current_vins:
                changes.removed.append({"vin": vin, **previous})

        changes.price_drops.sort(key=lambda c: c.delta)
        changes.price_rises.sort(key=lambda c: -c.delta)
        return changes

    # -- aggiornamento --------------------------------------------------------

    def update(self, state: dict[str, Any], valuations: list[Valuation]) -> dict[str, Any]:
        """Aggiorna lo stato con la fotografia attuale e lo restituisce."""
        timestamp = _now()
        vehicles: dict[str, Any] = state.get("vehicles", {})
        updated: dict[str, Any] = {}

        for valuation in valuations:
            listing = valuation.listing
            vin = listing.vin
            previous = vehicles.get(vin, {})
            price_history = list(previous.get("price_history", []))
            if valuation.price and (not price_history or price_history[-1][1] != valuation.price):
                price_history.append([timestamp, valuation.price])
            updated[vin] = {
                "first_seen": previous.get("first_seen", timestamp),
                "last_seen": timestamp,
                "price": valuation.price,
                "price_history": price_history[-MAX_PRICE_POINTS:],
                "score": valuation.score,
                "advantage_pct": round(valuation.advantage_pct, 2),
                "label": listing.label,
                "odometer_km": listing.odometer_km,
                "url": listing.url,
            }

        state["vehicles"] = updated
        state["last_run"] = timestamp
        return state

    def append_run(self, valuations: list[Valuation], changes: Changes, meta: dict[str, Any]) -> None:
        """Aggiunge una riga al registro delle esecuzioni (utile per i trend)."""
        best = max(valuations, key=lambda v: v.score, default=None)
        record = {
            "timestamp": _now(),
            "listings": len(valuations),
            "new": len(changes.new_listings),
            "price_drops": len(changes.price_drops),
            "price_rises": len(changes.price_rises),
            "removed": len(changes.removed),
            "backend": meta.get("backend"),
            "best_vin": best.listing.vin if best else None,
            "best_score": best.score if best else None,
            "best_price": best.price if best else None,
        }
        self.data_dir.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        if self.runs_path.exists():
            lines = self.runs_path.read_text(encoding="utf-8").splitlines()[-MAX_RUN_RECORDS:]
        lines.append(json.dumps(record, ensure_ascii=False))
        self.runs_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def price_history(self, state: dict[str, Any], vin: str) -> list[tuple[str, float]]:
        entry = state.get("vehicles", {}).get(vin, {})
        return [(str(t), float(p)) for t, p in entry.get("price_history", [])]
