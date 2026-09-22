"""Normalizzazione dei record grezzi dell'API Tesla in oggetti ``Listing``.

L'API non ha uno schema pubblico stabile: i nomi dei campi cambiano tra mercati
e versioni. Per questo ogni valore viene cercato su piu chiavi alternative e gli
optional sono riconosciuti sia dal codice ($APF2...) sia dal nome localizzato.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable

# --- allestimenti ------------------------------------------------------------

TRIM_RWD = "RWD"
TRIM_LR = "LR"
TRIM_PERF = "PERF"
TRIM_LABELS = {
    TRIM_RWD: "Trazione posteriore",
    TRIM_LR: "Long Range AWD",
    TRIM_PERF: "Performance",
}

# --- riconoscimento optional -------------------------------------------------

FSD_CODES = {"$APF2", "$APFB", "$APF3"}
EAP_CODES = {"$APBS", "$APPB", "$APBF"}
TOW_CODES = {"$TW01", "$TW02"}
PREMIUM_PAINT_CODES = {"$PPMR", "$PR01"}                 # rosso multistrato / Ultra Red
STANDARD_PAINT_CODES = {"$PPSW", "$PPSB", "$PMNG", "$PN00", "$PN01", "$PBCW"}
WHITE_INTERIOR_CODES = {"$IWW1", "$IPW1", "$IWB1"}
BASE_WHEEL_CODES = {"$W38B", "$W32P", "$W33D"}           # cerchi 18" di serie

# --- colori ------------------------------------------------------------------

# Chiavi colore usate dai filtri. I codici sono il segnale piu stabile, i nomi
# quello piu leggibile: si prova prima il codice, poi il nome della SOLA voce
# vernice (non di tutti gli optional insieme, altrimenti "Interni Neri"
# farebbe passare per nera un'auto bianca).
# Regola seguita nel compilare questa tabella: un codice sbagliato e peggio di un
# codice mancante. Se manca, il nome della vernice fa da rete di sicurezza; se e
# sbagliato, l'auto viene classificata male in silenzio. Per questo i codici
# della famiglia $PN* (grigio Stealth e argento) NON compaiono qui: le fonti
# consultate si contraddicono su quale sia l'uno e quale l'altro, e per quei due
# colori il nome commerciale e un segnale piu sicuro.
COLOR_CODES: dict[str, set[str]] = {
    "black": {"$PBSB", "$PMBL", "$PX02"},
    "white": {"$PPSW", "$PBCW"},
    "blue": {"$PPSB", "$PB00", "$PB01", "$PB02"},
    "red": {"$PPMR", "$PR00", "$PR01"},
    "grey": {"$PMNG", "$PMTG"},
    "silver": set(),
}

# L'ordine conta: "Grigio Midnight Silver" e un grigio, "Quicksilver" un argento,
# quindi le voci piu specifiche vanno provate per prime.
COLOR_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # "Grigio Midnight Silver" contiene sia "grigio" sia "silver": vince il
    # grigio perche e la parola esplicita. "Argento vivo" e "Quicksilver", che
    # il grigio non lo nominano, restano argento.
    ("grey", (r"\bgrigio\b", r"\bgr[ea]y\b")),
    ("silver", (r"quicksilver", r"\bargento\b", r"\bsilver\b")),
    ("red", (r"\brosso\b", r"\bred\b")),
    ("blue", (r"\bblu\b", r"\bblue\b")),
    ("black", (r"\bner[oa]\b", r"\bblack\b")),
    ("white", (r"\bbianc[oa]\b", r"\bwhite\b")),
)

COLOR_LABELS = {
    "black": "nero", "white": "bianco", "blue": "blu",
    "red": "rosso", "grey": "grigio", "silver": "argento",
}

PAINT_GROUPS = {"PAINT", "VERNICE", "EXTERIOR", "COLOR", "COLOUR"}

# --- generazione -------------------------------------------------------------

GEN_HIGHLAND = "highland"          # restyling presentato a settembre 2023
GEN_PRE = "pre-restyling"          # progetto originale 2017-2023
GEN_LABELS = {GEN_HIGHLAND: "Highland (restyling)", GEN_PRE: "pre-restyling"}

# Nel 2023 convivono le due generazioni: le immatricolazioni italiane fino a
# ottobre sono pre-restyling, poi arrivano le Highland. A parita di anno e
# chilometri la Highland vale di piu, quindi confonderle farebbe passare per
# occasioni delle pre-restyling che costano meno perche valgono meno.
# L'autonomia WLTP dichiarata e il criterio piu affidabile fra quelli presenti
# nell'annuncio: la RWD passa da 491/510 km a 513/554 km.
GENERATION_RANGE_SPLIT = {
    TRIM_RWD: 512.0,
    TRIM_LR: 615.0,
}
FACELIFT_FIRST_FULL_YEAR = 2024    # dal 2024 in poi e sicuramente Highland
PRE_FACELIFT_LAST_FULL_YEAR = 2022 # fino al 2022 e sicuramente pre-restyling


def detect_generation(trim: str, year: int | None, range_km: float | None) -> str:
    """Generazione del veicolo, o stringa vuota se non determinabile."""
    if year is not None:
        if year >= FACELIFT_FIRST_FULL_YEAR:
            return GEN_HIGHLAND
        if year <= PRE_FACELIFT_LAST_FULL_YEAR:
            return GEN_PRE
    # Resta il 2023, l'anno in cui le due generazioni si sovrappongono.
    split = GENERATION_RANGE_SPLIT.get(trim)
    if split and range_km:
        return GEN_HIGHLAND if range_km >= split else GEN_PRE
    return ""

FSD_PATTERNS = (r"guida autonoma", r"full self[- ]driving", r"\bfsd\b")
EAP_PATTERNS = (r"autopilot avanzato", r"enhanced autopilot")
TOW_PATTERNS = (r"gancio (di )?traino", r"tow hitch")
BOOST_PATTERNS = (r"acceleration boost", r"potenziamento dell'accelerazione",
                  r"accelerazione potenziata")
PREMIUM_PAINT_PATTERNS = (r"rosso multistrato", r"red multi", r"ultra red")
WHITE_INTERIOR_PATTERNS = (r"interni bianchi", r"white interior", r"bianco e nero")
UPGRADED_WHEEL_PATTERNS = (r"\b(19|20|21)\s*(\"|''|pollici|inch)",)


def _matches(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(p, text) for p in patterns)


# --- estrazione difensiva ----------------------------------------------------

def first_of(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Primo valore non vuoto tra le chiavi indicate (confronto case-insensitive)."""
    lowered = {str(k).lower(): v for k, v in data.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value not in (None, "", [], {}):
            return value
    return default


def to_float(value: Any) -> float | None:
    """Converte in float numeri che l'API puo restituire come stringa formattata."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r"[^\d,.\-]", "", text)
    if not text:
        return None
    # Separatori: il piu a destra e il decimale quando ne compaiono due tipi.
    # Con un solo tipo, un gruppo finale di 3 cifre indica le migliaia
    # ("29.900" -> 29900), mentre gruppi piu corti indicano i decimali ("5.6").
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    else:
        for sep in (",", "."):
            if sep not in text:
                continue
            groups = text.split(sep)
            is_thousands = len(groups) > 2 or len(groups[-1]) == 3
            text = text.replace(sep, "") if is_thousands else text.replace(sep, ".")
            break
    try:
        return float(text)
    except ValueError:
        return None


def to_int(value: Any) -> int | None:
    number = to_float(value)
    return int(round(number)) if number is not None else None


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[: len(fmt) + 2].rstrip("Z"), fmt).date()
        except ValueError:
            continue
    match = re.search(r"(20\d{2})-(\d{2})-(\d{2})", text)
    if match:
        try:
            return date(*(int(g) for g in match.groups()))
        except ValueError:
            return None
    return None


# --- modello -----------------------------------------------------------------

@dataclass
class Listing:
    """Un veicolo dell'inventario, normalizzato."""

    vin: str
    year: int | None = None
    trim: str = TRIM_LR
    trim_name: str = ""
    trim_guessed: bool = True
    odometer_km: int | None = None
    price: float | None = None          # prezzo di acquisto (contanti)
    fees: float = 0.0                   # trasporto / gestione pratica
    total_price: float | None = None    # prezzo + oneri
    currency: str = "EUR"
    range_km: float | None = None       # autonomia WLTP dichiarata
    acceleration_s: float | None = None
    city: str = ""
    region: str = ""
    delivery_date: date | None = None
    has_damage: bool = False
    is_demo: bool = False
    color: str = ""                     # black white blue red grey silver
    color_name: str = ""                # nome commerciale, es. "Nero Pastello"
    generation: str = ""                # highland | pre-restyling | "" se incerta
    option_codes: list[str] = field(default_factory=list)
    option_names: list[str] = field(default_factory=list)
    url: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    # optional riconosciuti
    has_fsd: bool = False
    has_eap: bool = False
    has_tow_hitch: bool = False
    has_acceleration_boost: bool = False
    has_premium_paint: bool = False
    has_standard_paint: bool = False
    has_white_interior: bool = False
    has_upgraded_wheels: bool = False

    @property
    def effective_price(self) -> float | None:
        """Prezzo usato per i calcoli: totale se noto, altrimenti di listino."""
        return self.total_price if self.total_price is not None else self.price

    @property
    def age_years(self) -> float | None:
        """Eta in anni, dalla data di consegna se disponibile, altrimenti dall'anno."""
        today = date.today()
        if self.delivery_date:
            return max(0.0, (today - self.delivery_date).days / 365.25)
        if self.year:
            return max(0.0, today.year - self.year + (today.month - 6) / 12)
        return None

    @property
    def label(self) -> str:
        parts = [str(self.year) if self.year else "?", "Model 3",
                 self.trim_name or TRIM_LABELS.get(self.trim, self.trim)]
        return " ".join(p for p in parts if p)


# --- costruzione del Listing -------------------------------------------------

def _collect_options(record: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Raccoglie codici e nomi degli optional da tutte le strutture note."""
    codes: list[str] = []
    names: list[str] = []
    entries: list[dict[str, str]] = []

    raw_list = first_of(record, "OptionCodeList", "OptionCodes", default="")
    if isinstance(raw_list, str) and raw_list:
        codes.extend(c.strip() for c in raw_list.split(",") if c.strip())
    elif isinstance(raw_list, list):
        codes.extend(str(c).strip() for c in raw_list if str(c).strip())

    option_data = first_of(record, "OptionCodeData", "OptionCodeDataV2", default=[])
    if isinstance(option_data, list):
        for entry in option_data:
            if not isinstance(entry, dict):
                continue
            code = entry.get("code") or entry.get("optionCode")
            if code:
                codes.append(str(code).strip())
            entry_names: list[str] = []
            for key in ("name", "long_name", "description", "value"):
                value = entry.get(key)
                if isinstance(value, str) and value.strip():
                    entry_names.append(value.strip())
            names.extend(entry_names)
            # Il gruppo serve al riconoscimento del colore, che deve guardare
            # solo la voce della vernice.
            entries.append({
                "code": str(code).strip().upper() if code else "",
                "group": str(entry.get("group") or entry.get("groupName") or "").upper(),
                "name": " ".join(entry_names),
            })

    specs = first_of(record, "OptionCodeSpecs", default={})
    if isinstance(specs, dict):
        for group in specs.values():
            if not isinstance(group, dict):
                continue
            for entry in group.get("options", []) or []:
                if not isinstance(entry, dict):
                    continue
                if entry.get("code"):
                    codes.append(str(entry["code"]).strip())
                for key in ("name", "long_name", "description"):
                    value = entry.get(key)
                    if isinstance(value, str) and value.strip():
                        names.append(value.strip())

    # dedup preservando l'ordine
    codes = list(dict.fromkeys(codes))
    names = list(dict.fromkeys(names))
    return codes, names, entries


def _detect_color(entries: list[dict[str, str]], codes_upper: set[str]) -> tuple[str, str]:
    """Colore della carrozzeria. Ritorna (chiave, nome commerciale).

    Prima il codice option, che e il dato piu stabile; poi il nome della voce
    vernice. Il nome va letto solo da quella voce: cercare "nero" fra tutti gli
    optional farebbe passare per nera un'auto bianca con gli interni neri.
    """
    for key, key_codes in COLOR_CODES.items():
        found = codes_upper & key_codes
        if found:
            code = next(iter(found))
            name = next((e["name"] for e in entries if e["code"] == code and e["name"]), "")
            return key, name

    paint_entries = [e for e in entries
                     if e["group"] in PAINT_GROUPS
                     or (not e["group"] and e["code"].startswith("$P"))]
    for entry in paint_entries:
        text = entry["name"].lower()
        for key, patterns in COLOR_PATTERNS:
            if _matches(text, patterns):
                return key, entry["name"]

    return "", ""


def _detect_trim(record: dict[str, Any], names: list[str], codes: list[str]) -> tuple[str, str, bool]:
    """Deduce l'allestimento. Ritorna (chiave, etichetta, e_stato_indovinato)."""
    explicit: list[str] = []
    for key in ("TrimName", "TRIM_NAME", "Trim", "TRIM", "TrimCode", "OptionCodeSpecsTrim"):
        value = record.get(key)
        if isinstance(value, str):
            explicit.append(value)
        elif isinstance(value, list):
            explicit.extend(str(v) for v in value)

    # I nomi degli optional aiutano a riconoscere l'allestimento, ma non possono
    # diventare l'etichetta: senza questa distinzione un'auto priva di TrimName
    # finirebbe per chiamarsi "Bianco Perla Multistrato".
    blob = " ".join(explicit + names).lower()
    label = next((c for c in explicit if len(c) > 3), "")

    if re.search(r"performance", blob):
        return TRIM_PERF, label or TRIM_LABELS[TRIM_PERF], False
    if re.search(r"long range|grande autonomia|dual motor|doppio motore|awd|integrale", blob):
        return TRIM_LR, label or TRIM_LABELS[TRIM_LR], False
    if re.search(r"standard range|rwd|trazione posteriore|propulsione posteriore|single motor", blob):
        return TRIM_RWD, label or TRIM_LABELS[TRIM_RWD], False

    # Fallback sui dati tecnici: la RWD sta sotto i 520 km WLTP.
    is_standard = first_of(record, "IsRangeStandard")
    if isinstance(is_standard, bool):
        trim = TRIM_RWD if is_standard else TRIM_LR
        return trim, label or TRIM_LABELS[trim], True
    return TRIM_LR, label or TRIM_LABELS[TRIM_LR], True


def _detect_specs(record: dict[str, Any]) -> tuple[float | None, float | None]:
    """Autonomia WLTP (km) e accelerazione 0-100 (s) dalle specifiche."""
    range_km = to_float(first_of(record, "Range", "RangeWltp", "EPARange"))
    accel = to_float(first_of(record, "Acceleration", "AccelerationSec"))

    specs = first_of(record, "OptionCodeSpecs", default={})
    if isinstance(specs, dict):
        for group in specs.values():
            if not isinstance(group, dict):
                continue
            for entry in group.get("options", []) or []:
                if not isinstance(entry, dict):
                    continue
                code = str(entry.get("code", "")).upper()
                name = str(entry.get("name", "")).lower()
                value = to_float(entry.get("value"))
                if value is None:
                    continue
                if "RANGE" in code or "autonomia" in name:
                    range_km = range_km or value
                elif "ACCELERATION" in code or "accelerazione" in name or "0-100" in name:
                    accel = accel or value
    return range_km, accel


def _listing_url(record: dict[str, Any], vin: str, market: str, language: str, zip_code: str) -> str:
    explicit = first_of(record, "VehicleDetailsUrl", "Url", "DetailsUrl")
    if isinstance(explicit, str) and explicit.startswith("http"):
        return explicit
    model = str(first_of(record, "Model", default="m3")).lower()
    return (f"https://www.tesla.com/{language}_{market}/{model}/order/{vin}"
            f"?postal={zip_code}&region={market}")


def parse_listing(record: dict[str, Any], *, market: str = "IT", language: str = "it",
                  zip_code: str = "") -> Listing | None:
    """Converte un record grezzo in ``Listing``. Ritorna None se manca il VIN."""
    vin = str(first_of(record, "VIN", "Vin", default="")).strip()
    if not vin:
        return None

    codes, names, entries = _collect_options(record)
    codes_upper = {c.upper() for c in codes}
    names_blob = " ".join(names).lower()

    trim, trim_name, guessed = _detect_trim(record, names, codes)
    color, color_name = _detect_color(entries, codes_upper)
    range_km, accel = _detect_specs(record)

    odometer = to_int(first_of(record, "Odometer", "Mileage", "OdometerKm"))
    odometer_type = str(first_of(record, "OdometerType", default="km")).lower()
    if odometer is not None and odometer_type.startswith("mi"):
        odometer = int(round(odometer * 1.60934))

    price = to_float(first_of(record, "PurchasePrice", "InventoryPrice", "Price",
                              "CountryOfferPrice", "TotalPrice"))
    fees = 0.0
    for key in ("TransportFee", "DestinationFee", "AdminFee", "OrderFee", "DocumentFee"):
        fee = to_float(record.get(key))
        if fee:
            fees += fee
    total = to_float(first_of(record, "TotalPrice"))
    if total is None and price is not None:
        total = price + fees

    damage = bool(first_of(record, "HasDamagePhotos", default=False)) or bool(
        first_of(record, "DamageDisclosure", default=False))

    listing = Listing(
        vin=vin,
        year=to_int(first_of(record, "Year", "ModelYear")),
        trim=trim,
        trim_name=trim_name,
        trim_guessed=guessed,
        odometer_km=odometer,
        price=price,
        fees=fees,
        total_price=total,
        currency=str(first_of(record, "CurrencyCode", default="EUR")),
        range_km=range_km,
        acceleration_s=accel,
        city=str(first_of(record, "City", "MetroName", default="")),
        region=str(first_of(record, "StateProvince", "CountryCode", default="")),
        delivery_date=_parse_date(first_of(record, "OriginalDeliveryDate",
                                           "FirstRegistrationDate", "DeliveryDateDisplay")),
        has_damage=damage,
        is_demo=bool(first_of(record, "IsDemo", default=False)),
        color=color,
        color_name=color_name,
        generation=detect_generation(trim, to_int(first_of(record, "Year", "ModelYear")),
                                     range_km),
        option_codes=codes,
        option_names=names,
        url=_listing_url(record, vin, market, language, zip_code),
        raw=record,
    )

    listing.has_fsd = bool(codes_upper & FSD_CODES) or _matches(names_blob, FSD_PATTERNS)
    listing.has_eap = bool(codes_upper & EAP_CODES) or _matches(names_blob, EAP_PATTERNS)
    listing.has_tow_hitch = bool(codes_upper & TOW_CODES) or _matches(names_blob, TOW_PATTERNS)
    listing.has_acceleration_boost = _matches(names_blob, BOOST_PATTERNS)
    listing.has_premium_paint = (bool(codes_upper & PREMIUM_PAINT_CODES)
                                 or _matches(names_blob, PREMIUM_PAINT_PATTERNS))
    listing.has_standard_paint = (not listing.has_premium_paint
                                  and bool(codes_upper & STANDARD_PAINT_CODES))
    listing.has_white_interior = (bool(codes_upper & WHITE_INTERIOR_CODES)
                                  or _matches(names_blob, WHITE_INTERIOR_PATTERNS))
    # I cerchi di serie sono da 18": conta come optional solo una misura maggiore.
    # Il nome ("Cerchi Sport da 19\"") e il segnale piu affidabile; quando fra gli
    # optional non si parla di cerchi si ripiega sui codici, escludendo quelli base.
    if re.search(r"cerchi|wheels", names_blob):
        listing.has_upgraded_wheels = _matches(names_blob, UPGRADED_WHEEL_PATTERNS)
    else:
        wheel_codes = {c for c in codes_upper if c.startswith("$W")}
        listing.has_upgraded_wheels = bool(wheel_codes - BASE_WHEEL_CODES)
    # FSD implica le funzioni dell'Autopilot avanzato: evitiamo di contarle due volte.
    if listing.has_fsd:
        listing.has_eap = False
    return listing


def parse_all(records: list[dict[str, Any]], *, market: str = "IT", language: str = "it",
              zip_code: str = "") -> list[Listing]:
    listings = []
    for record in records:
        listing = parse_listing(record, market=market, language=language, zip_code=zip_code)
        if listing is not None:
            listings.append(listing)
    return listings
