"""Stima del valore di mercato di un'auto usata e punteggio qualita/prezzo.

Il modello e volutamente esplicito e componibile: il valore stimato nasce dal
listino del nuovo, deprezzato per eta, corretto per percorrenza, optional,
garanzia residua e danni dichiarati. La convenienza e la differenza percentuale
fra valore stimato e prezzo richiesto; il punteggio finale combina convenienza e
qualita intrinseca dell'auto.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Config, ScoreConfig, ValuationConfig
from .parse import GEN_LABELS, GEN_PRE, Listing, TRIM_LABELS


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


@dataclass
class Valuation:
    """Esito della valutazione di un annuncio, con il dettaglio dei passaggi."""

    listing: Listing
    valid: bool = True
    notes: list[str] = field(default_factory=list)

    reference_new_price: float = 0.0
    retention: float = 0.0
    depreciated_value: float = 0.0
    km_adjustment: float = 0.0
    options_value: float = 0.0
    warranty_value: float = 0.0
    damage_penalty: float = 0.0
    generation_adjustment: float = 0.0
    estimated_value: float = 0.0

    calibration: float = 1.0
    raw_estimated_value: float = 0.0

    price: float = 0.0
    advantage_eur: float = 0.0
    advantage_pct: float = 0.0

    score: float = 0.0
    score_parts: dict[str, float] = field(default_factory=dict)

    @property
    def price_per_range_km(self) -> float | None:
        """Euro per km di autonomia WLTP: piu basso e meglio."""
        if self.price and self.listing.range_km:
            return self.price / self.listing.range_km
        return None


# --- componenti del valore ---------------------------------------------------

def reference_new_price(cfg: ValuationConfig, trim: str) -> tuple[float, str | None]:
    """Listino attuale del nuovo per l'allestimento indicato."""
    price = cfg.new_prices.get(trim)
    if price:
        return float(price), None
    fallback = cfg.new_prices.get("LR") or next(iter(cfg.new_prices.values()), 0.0)
    return float(fallback), "nessun listino per l'allestimento: usato quello Long Range"


def retention_factor(cfg: ValuationConfig, age_years: float) -> float:
    """Quota di valore residua dopo ``age_years`` anni."""
    if age_years <= 0:
        return 1.0
    if age_years <= 1:
        # Interpolazione lineare fra nuovo e fine del primo anno.
        return 1.0 - (1.0 - cfg.first_year_retention) * age_years
    factor = cfg.first_year_retention * (cfg.annual_retention ** (age_years - 1))
    return max(cfg.min_retention, factor)


def mileage_adjustment(cfg: ValuationConfig, listing: Listing, age_years: float,
                       base_value: float) -> float:
    """Correzione (positiva o negativa) per la percorrenza rispetto all'attesa."""
    if listing.odometer_km is None:
        return 0.0
    expected = cfg.expected_km_per_year * max(age_years, 0.5)
    delta = listing.odometer_km - expected
    adjustment = -delta * cfg.cost_per_excess_km
    cap = base_value * cfg.km_adjustment_cap_pct
    return max(-cap, min(cap, adjustment))


def options_value(cfg: ValuationConfig, listing: Listing) -> float:
    """Valore residuo degli optional presenti."""
    values = cfg.option_values
    total = 0.0
    if listing.has_fsd:
        total += values.get("fsd", 0.0)
    if listing.has_eap:
        total += values.get("eap", 0.0)
    if listing.has_tow_hitch:
        total += values.get("tow_hitch", 0.0)
    if listing.has_acceleration_boost:
        total += values.get("acceleration_boost", 0.0)
    # Il prezzo dichiarato dall'annuncio batte la classificazione per codice:
    # quali colori siano a pagamento cambia da mercato a mercato e nel tempo.
    if listing.paint_price is not None:
        total += listing.paint_price * cfg.paint_value_fraction
    elif listing.has_premium_paint:
        total += values.get("premium_paint", 0.0)
    elif listing.has_standard_paint:
        total += values.get("standard_paint", 0.0)
    if listing.has_white_interior:
        total += values.get("white_interior", 0.0)
    if listing.has_upgraded_wheels:
        total += values.get("upgraded_wheels", 0.0)
    return total


def warranty_remaining(cfg: ValuationConfig, listing: Listing, age_years: float) -> float:
    """Frazione 0-1 di garanzia batteria/motore ancora disponibile.

    Vale il limite raggiunto per primo fra anni e chilometri.
    """
    years_left = max(0.0, cfg.battery_warranty_years - age_years) / cfg.battery_warranty_years
    km_limit = cfg.battery_warranty_km.get(listing.trim, 160000)
    if listing.odometer_km is None:
        km_left = years_left
    else:
        km_left = max(0.0, km_limit - listing.odometer_km) / km_limit
    return min(years_left, km_left)


# --- valutazione di un annuncio ---------------------------------------------

def evaluate(cfg: Config, listing: Listing) -> Valuation:
    """Calcola valore stimato e convenienza di un singolo annuncio."""
    vcfg = cfg.valuation
    result = Valuation(listing=listing)

    price = listing.effective_price
    if not price or price <= 0:
        result.valid = False
        result.notes.append("prezzo non disponibile")
        return result
    result.price = price

    age = listing.age_years
    if age is None:
        result.valid = False
        result.notes.append("eta non determinabile (anno e data di consegna assenti)")
        return result

    base, base_note = reference_new_price(vcfg, listing.trim)
    if base_note:
        result.notes.append(base_note)
    if listing.trim_guessed:
        result.notes.append("allestimento dedotto dai dati tecnici")
    if listing.odometer_km is None:
        result.notes.append("chilometraggio non dichiarato")

    result.reference_new_price = base
    result.retention = retention_factor(vcfg, age)
    result.depreciated_value = base * result.retention
    result.km_adjustment = mileage_adjustment(vcfg, listing, age, result.depreciated_value)
    result.options_value = options_value(vcfg, listing)
    result.warranty_value = warranty_remaining(vcfg, listing, age) * vcfg.full_warranty_value
    result.damage_penalty = vcfg.damage_penalty if listing.has_damage else 0.0
    if listing.has_damage:
        result.notes.append("danni dichiarati dal venditore")

    if listing.generation == GEN_PRE:
        result.generation_adjustment = -vcfg.pre_facelift_discount
    elif not listing.generation:
        result.notes.append("generazione non determinabile: potrebbe essere "
                            "pre-restyling o Highland")

    result.raw_estimated_value = max(
        1.0,
        result.depreciated_value + result.km_adjustment + result.options_value
        + result.warranty_value + result.generation_adjustment - result.damage_penalty,
    )
    result.estimated_value = result.raw_estimated_value
    _recompute_advantage(result)
    return result


def _recompute_advantage(result: Valuation) -> None:
    result.advantage_eur = result.estimated_value - result.price
    result.advantage_pct = result.advantage_eur / result.estimated_value * 100.0


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def calibrate(cfg: Config, valuations: list[Valuation]) -> float:
    """Riallinea le stime all'inventario osservato e ritorna il fattore mediano.

    Il modello di deprezzamento e per forza approssimato: listini che cambiano,
    allestimenti dedotti, optional non sempre dichiarati. Riscalando le stime in
    modo che l'auto mediana risulti a convenienza zero, la convenienza diventa
    una misura relativa - quanto quest'auto costa meno di quello che ci si
    aspetterebbe viste le altre in vendita nello stesso momento - che e proprio
    la domanda a cui serve rispondere.

    La calibrazione e per ALLESTIMENTO quando i dati bastano, con ricaduta sul
    fattore globale altrimenti. Un fattore unico per tutta la gamma trasferirebbe
    l'errore del listino di riferimento di un allestimento su tutti gli altri:
    se l'ancora della trazione posteriore fosse troppo bassa, tutte le RWD
    risulterebbero care rispetto alle Long Range e non verrebbero mai segnalate,
    per un errore di taratura e non per il loro prezzo.
    """
    vcfg = cfg.valuation
    valid = [v for v in valuations if v.valid and v.raw_estimated_value > 0 and v.price > 0]
    if not vcfg.auto_calibrate or len(valid) < vcfg.calibration_min_samples:
        return 1.0

    global_factor = _median([v.price / v.raw_estimated_value for v in valid])
    if global_factor <= 0:
        return 1.0

    by_trim: dict[str, list[Valuation]] = {}
    for valuation in valid:
        by_trim.setdefault(valuation.listing.trim, []).append(valuation)

    factors: dict[str, float] = {}
    for trim, group in by_trim.items():
        if len(group) >= vcfg.calibration_min_samples:
            trim_factor = _median([v.price / v.raw_estimated_value for v in group])
            factors[trim] = trim_factor if trim_factor > 0 else global_factor
        else:
            factors[trim] = global_factor

    for valuation in valid:
        factor = factors.get(valuation.listing.trim, global_factor)
        valuation.calibration = factor
        valuation.estimated_value = valuation.raw_estimated_value * factor
        _recompute_advantage(valuation)
    return global_factor


# --- punteggio ---------------------------------------------------------------

def _deal_score(cfg: ScoreConfig, advantage_pct: float) -> float:
    return _clamp(50.0 + (advantage_pct / cfg.deal_span_pct) * 50.0)


def _mileage_score(vcfg: ValuationConfig, listing: Listing, age_years: float) -> float:
    if listing.odometer_km is None:
        return 50.0
    expected = max(vcfg.expected_km_per_year * max(age_years, 0.5), 1.0)
    ratio_score = _clamp(100.0 - 40.0 * (listing.odometer_km / expected))
    absolute_score = _clamp(100.0 - listing.odometer_km / 2000.0)
    return 0.5 * ratio_score + 0.5 * absolute_score


def _equipment_score(listing: Listing, opt_value: float) -> float:
    equipment = min(opt_value / 2500.0, 1.0) * 60.0
    if listing.range_km:
        autonomy = _clamp((listing.range_km - 400.0) / 250.0, 0.0, 1.0) * 40.0
    else:
        autonomy = 20.0
    return _clamp(equipment + autonomy)


def compute_score(cfg: Config, valuation: Valuation) -> Valuation:
    """Assegna il punteggio 0-100 combinando convenienza e qualita."""
    if not valuation.valid:
        return valuation

    listing = valuation.listing
    age = listing.age_years or 0.0
    scfg = cfg.score

    parts = {
        "convenienza": _deal_score(scfg, valuation.advantage_pct),
        "percorrenza": _mileage_score(cfg.valuation, listing, age),
        "garanzia": _clamp(warranty_remaining(cfg.valuation, listing, age) * 100.0),
        "dotazione": _equipment_score(listing, valuation.options_value),
    }
    weights = {
        "convenienza": scfg.weight_deal,
        "percorrenza": scfg.weight_mileage,
        "garanzia": scfg.weight_warranty,
        "dotazione": scfg.weight_equipment,
    }
    total_weight = sum(weights.values()) or 1.0
    valuation.score_parts = {k: round(v, 1) for k, v in parts.items()}
    valuation.score = round(sum(parts[k] * weights[k] for k in parts) / total_weight, 1)
    return valuation


def evaluate_all(cfg: Config, listings: list[Listing]) -> tuple[list[Valuation], float]:
    """Valuta, calibra e assegna il punteggio a tutti gli annunci.

    Ritorna (valutazioni, fattore_di_calibrazione).
    """
    valuations = [evaluate(cfg, listing) for listing in listings]
    factor = calibrate(cfg, valuations)
    for valuation in valuations:
        compute_score(cfg, valuation)
    return valuations, factor


def rank(valuations: list[Valuation], mode: str = "score") -> list[Valuation]:
    """Ordina dal migliore al peggiore secondo il criterio scelto."""
    valid = [v for v in valuations if v.valid]
    if mode == "deal":
        key = lambda v: (-v.advantage_pct, -v.score)
    elif mode == "price":
        key = lambda v: (v.price, -v.score)
    else:
        key = lambda v: (-v.score, -v.advantage_pct)
    return sorted(valid, key=key)


def describe_trim(listing: Listing) -> str:
    return listing.trim_name or TRIM_LABELS.get(listing.trim, listing.trim)


def describe_generation(listing: Listing) -> str:
    """Etichetta leggibile della generazione, vuota se non determinabile."""
    return GEN_LABELS.get(listing.generation, "")
