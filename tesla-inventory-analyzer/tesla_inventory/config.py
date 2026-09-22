"""Configurazione dell'analizzatore: ricerca, valutazione e output.

Tutti i parametri sono sovrascrivibili da file JSON (--config) o da CLI, cosi da
poter tarare il modello di valutazione senza toccare il codice.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


# --- Ricerca -----------------------------------------------------------------

@dataclass
class SearchConfig:
    """Parametri della query verso l'inventario Tesla."""

    model: str = "m3"           # m3 = Model 3, my = Model Y, ms = Model S, mx = Model X
    condition: str = "used"     # used | new
    market: str = "IT"
    language: str = "it"
    zip_code: str = "90126"     # CAP di riferimento (Palermo)
    arrangeby: str = "Plh"      # Plh = prezzo crescente
    order: str = "asc"
    payment_type: str = "cash"
    range_km: int = 0           # 0 = nessun limite di distanza
    count: int = 50             # risultati per pagina
    max_pages: int = 10         # tetto di sicurezza sulla paginazione


# --- Filtri ------------------------------------------------------------------

@dataclass
class FilterConfig:
    """Filtri applicati dopo il download, prima della classifica."""

    max_price: float | None = None
    min_price: float | None = None
    max_odometer: int | None = None
    min_year: int | None = None
    max_year: int | None = None
    trims: list[str] = field(default_factory=list)   # RWD | LR | PERF
    exclude_damaged: bool = False


# --- Valutazione -------------------------------------------------------------

# Prezzo di listino ATTUALE del nuovo in Italia (EUR, IVA inclusa, allestimento
# base). Il valore dell'usato viene ancorato qui, non al listino dell'anno di
# immatricolazione: Tesla ha tagliato i listini nel 2023, quindi partire dal
# prezzo storico sovrastima il deprezzamento. Vanno aggiornati se Tesla cambia
# i prezzi: sono tre numeri leggibili direttamente da tesla.com.
DEFAULT_NEW_PRICES: dict[str, float] = {
    "RWD": 42490.0,
    "LR": 50490.0,
    "PERF": 57490.0,
}

# Valore residuo stimato degli optional sul mercato dell'usato (EUR).
DEFAULT_OPTION_VALUES: dict[str, float] = {
    "fsd": 2500.0,              # Guida Autonoma Totale
    "eap": 1200.0,              # Autopilot Avanzato
    "tow_hitch": 700.0,         # Gancio traino
    "acceleration_boost": 500.0,
    "premium_paint": 400.0,     # Rosso multistrato
    "standard_paint": 150.0,    # Bianco / blu / grigio
    "white_interior": 400.0,
    "upgraded_wheels": 300.0,   # Cerchi 19" / 20"
}


@dataclass
class ValuationConfig:
    """Parametri del modello che stima il valore di mercato di un'auto."""

    # Deprezzamento rispetto al listino attuale del nuovo:
    # valore residuo = primo_anno * annuo^(eta-1).
    # I valori di partenza riflettono la curva tipica della Model 3 in Europa
    # (circa 82% a un anno, 63% a tre, 48% a cinque).
    first_year_retention: float = 0.82
    annual_retention: float = 0.875
    min_retention: float = 0.22

    # Percorrenza
    expected_km_per_year: int = 15000
    cost_per_excess_km: float = 0.055     # EUR di valore persi per km oltre l'atteso
    km_adjustment_cap_pct: float = 0.25   # tetto della correzione km (+/- 25% del valore base)

    # Garanzia batteria e motore: 8 anni, 160.000 km (RWD) / 192.000 km (LR, Perf)
    battery_warranty_years: int = 8
    battery_warranty_km: dict[str, int] = field(
        default_factory=lambda: {"RWD": 160000, "LR": 192000, "PERF": 192000}
    )
    full_warranty_value: float = 1500.0   # valore attribuito a garanzia interamente residua

    # Penalita per danni dichiarati
    damage_penalty: float = 800.0

    new_prices: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_NEW_PRICES)
    )

    # Auto-calibrazione: riallinea la stima in modo che l'auto mediana
    # dell'inventario risulti a convenienza zero. Rende il confronto robusto
    # anche se i listini di riferimento sono un po' datati.
    auto_calibrate: bool = True
    calibration_min_samples: int = 4
    option_values: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_OPTION_VALUES)
    )


@dataclass
class ScoreConfig:
    """Pesi delle componenti del punteggio finale 0-100."""

    weight_deal: float = 0.45       # convenienza rispetto al valore stimato
    weight_mileage: float = 0.20    # percorrenza
    weight_warranty: float = 0.20   # garanzia/eta residua
    weight_equipment: float = 0.15  # dotazione e autonomia

    # Mappatura della convenienza: -deal_span% -> 0 punti, 0% -> 50, +deal_span% -> 100
    deal_span_pct: float = 15.0


# --- Output ------------------------------------------------------------------

@dataclass
class OutputConfig:
    data_dir: str = "data"
    top_n: int = 5                  # quante auto nel riepilogo dettagliato
    table_rows: int = 15            # quante righe nella tabella di classifica
    write_markdown: bool = True
    write_html: bool = True
    save_raw: bool = False          # salva la risposta grezza dell'API
    color: bool = True


@dataclass
class NotifyConfig:
    """Notifiche opzionali: disattivate se mancano i parametri."""

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    webhook_url: str = ""
    only_on_change: bool = True     # notifica solo se ci sono novita rilevanti


@dataclass
class FetchConfig:
    backend: str = "http"           # http | browser | file
    timeout: int = 30
    retries: int = 3
    retry_backoff: float = 2.0
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"
    )
    source_file: str = ""           # usato da backend=file (test offline)
    browser_wait_ms: int = 20000    # attesa per il backend browser


@dataclass
class Config:
    search: SearchConfig = field(default_factory=SearchConfig)
    filters: FilterConfig = field(default_factory=FilterConfig)
    valuation: ValuationConfig = field(default_factory=ValuationConfig)
    score: ScoreConfig = field(default_factory=ScoreConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    fetch: FetchConfig = field(default_factory=FetchConfig)
    interval_hours: float = 3.0     # cadenza della modalita --loop

    # -- serializzazione ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        """Costruisce una Config applicando solo le chiavi presenti nel dict."""
        cfg = cls()
        sections = {
            "search": cfg.search, "filters": cfg.filters, "valuation": cfg.valuation,
            "score": cfg.score, "output": cfg.output, "notify": cfg.notify,
            "fetch": cfg.fetch,
        }
        for name, section in sections.items():
            for key, value in (data.get(name) or {}).items():
                if key.startswith("_"):
                    continue          # le chiavi con underscore sono commenti
                if not hasattr(section, key):
                    raise ValueError(f"Chiave di configurazione sconosciuta: {name}.{key}")
                setattr(section, key, value)
        if "interval_hours" in data:
            cfg.interval_hours = float(data["interval_hours"])
        return cfg

    @classmethod
    def load(cls, path: str | Path | None) -> "Config":
        if not path:
            return cls()
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(raw)
