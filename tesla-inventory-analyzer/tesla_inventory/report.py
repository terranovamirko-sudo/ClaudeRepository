"""Presentazione dei risultati: console, Markdown, HTML e testo per le notifiche."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .config import Config
from .fetch import page_url
from .history import Changes
from .parse import Listing
from .valuation import Valuation, describe_trim

# --- formattazione numerica (convenzioni italiane) ---------------------------

def fmt_int(value: float | int | None) -> str:
    if value is None:
        return "n.d."
    return f"{int(round(value)):,}".replace(",", ".")


def eur(value: float | None) -> str:
    return "n.d." if value is None else f"{fmt_int(value)} €"


def km(value: float | None) -> str:
    return "n.d." if value is None else f"{fmt_int(value)} km"


def pct(value: float | None, signed: bool = True) -> str:
    if value is None:
        return "n.d."
    text = f"{value:+.1f}" if signed else f"{value:.1f}"
    return text.replace(".", ",") + "%"


def num(value: float | None, decimals: int = 1) -> str:
    if value is None:
        return "n.d."
    return f"{value:.{decimals}f}".replace(".", ",")


def plural(count: int, singular: str, plural_form: str) -> str:
    """"1 annuncio" / "3 annunci": evita i plurali sbagliati nei riepiloghi."""
    return f"{count} {singular if count == 1 else plural_form}"


def search_description(cfg: Config) -> str:
    model_names = {"m3": "Model 3", "my": "Model Y", "ms": "Model S", "mx": "Model X"}
    model = model_names.get(cfg.search.model, cfg.search.model.upper())
    condition = "usate" if cfg.search.condition == "used" else "nuove"
    payment = "contanti" if cfg.search.payment_type.lower() == "cash" else cfg.search.payment_type
    return f"{model} {condition} · CAP {cfg.search.zip_code} · pagamento {payment}"


def equipment_tags(listing: Listing) -> list[str]:
    tags = []
    if listing.has_fsd:
        tags.append("Guida Autonoma Totale")
    if listing.has_eap:
        tags.append("Autopilot Avanzato")
    if listing.has_acceleration_boost:
        tags.append("Acceleration Boost")
    if listing.has_tow_hitch:
        tags.append("Gancio traino")
    if listing.has_white_interior:
        tags.append("Interni bianchi")
    if listing.has_upgraded_wheels:
        tags.append("Cerchi maggiorati")
    if listing.has_premium_paint:
        tags.append("Vernice premium")
    return tags


# --- console -----------------------------------------------------------------

class _Style:
    """Codici ANSI, disattivabili quando l'output non e un terminale."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def bold(self, t: str) -> str: return self._wrap("1", t)
    def dim(self, t: str) -> str: return self._wrap("2", t)
    def green(self, t: str) -> str: return self._wrap("32", t)
    def red(self, t: str) -> str: return self._wrap("31", t)
    def yellow(self, t: str) -> str: return self._wrap("33", t)
    def cyan(self, t: str) -> str: return self._wrap("36", t)


def _score_bar(score: float, width: int = 10) -> str:
    filled = int(round(score / 100 * width))
    return "█" * filled + "░" * (width - filled)


def format_console(cfg: Config, ranked: list[Valuation], skipped: list[Valuation],
                   changes: Changes, meta: dict[str, Any]) -> str:
    s = _Style(cfg.output.color)
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    lines: list[str] = []
    rule = "═" * 78

    lines.append(s.cyan(rule))
    lines.append(s.bold(f"  TESLA — ANALISI INVENTARIO USATO  ·  {now}"))
    lines.append(f"  {search_description(cfg)}")
    lines.append(s.cyan(rule))
    lines.append("")

    total = len(ranked) + len(skipped)
    lines.append(f"  {s.bold(str(total))} auto trovate · {len(ranked)} valutate"
                 + (f" · {len(skipped)} senza dati sufficienti" if skipped else ""))

    if not ranked:
        lines.append("")
        lines.append(s.yellow("  Nessuna auto valutabile con i filtri attuali."))
        lines.append("")
        return "\n".join(lines)

    # --- novita dall'ultimo controllo ---
    lines.append("")
    if changes.first_run:
        lines.append(s.dim("  Primo controllo: da adesso segnalo nuovi annunci e variazioni di prezzo."))
    elif not changes.has_news:
        lines.append(s.dim(f"  Nessuna novita dall'ultimo controllo ({changes.previous_run or 'n.d.'})."))
    else:
        lines.append(s.bold("  NOVITA DALL'ULTIMO CONTROLLO"))
        for valuation in changes.new_listings[:8]:
            lines.append(s.green(f"    + Nuovo   {valuation.listing.label} — "
                                 f"{eur(valuation.price)} · punteggio {num(valuation.score)}"))
        for change in changes.price_drops[:8]:
            lines.append(s.green(
                f"    ↓ Ribasso {change.valuation.listing.label} — "
                f"{eur(change.old_price)} → {eur(change.new_price)} "
                f"({eur(change.delta)}, {pct(change.delta_pct)})"))
        for change in changes.price_rises[:5]:
            lines.append(s.red(
                f"    ↑ Rincaro {change.valuation.listing.label} — "
                f"{eur(change.old_price)} → {eur(change.new_price)} ({pct(change.delta_pct)})"))
        for gone in changes.removed[:5]:
            lines.append(s.dim(f"    − Sparita  {gone.get('label', gone.get('vin'))} "
                               f"— era a {eur(gone.get('price'))}"))

    # --- migliori occasioni ---
    lines.append("")
    lines.append(s.cyan("─" * 78))
    lines.append(s.bold("  MIGLIOR RAPPORTO QUALITA/PREZZO"))
    lines.append(s.cyan("─" * 78))

    for position, valuation in enumerate(ranked[: cfg.output.top_n], start=1):
        listing = valuation.listing
        medal = {1: "\U0001F947", 2: "\U0001F948", 3: "\U0001F949"}.get(position, f"{position}.")
        lines.append("")
        lines.append(f"  {medal} {s.bold(listing.label)}")
        lines.append(f"     Punteggio   {s.bold(num(valuation.score))}/100  "
                     f"{s.cyan(_score_bar(valuation.score))}")
        lines.append(f"     Prezzo      {s.bold(eur(valuation.price))}"
                     + (f"  (di cui {eur(listing.fees)} di oneri)" if listing.fees else ""))
        advantage = (s.green if valuation.advantage_eur >= 0 else s.red)(
            f"{eur(valuation.advantage_eur)} ({pct(valuation.advantage_pct)})")
        lines.append(f"     Valore stimato {eur(valuation.estimated_value)} → "
                     f"convenienza {advantage}")
        lines.append(f"     Percorrenza {km(listing.odometer_km)}"
                     + (f" · autonomia {fmt_int(listing.range_km)} km WLTP" if listing.range_km else ""))
        details = " · ".join(f"{k} {num(v, 0)}" for k, v in valuation.score_parts.items())
        lines.append(s.dim(f"     Dettaglio punteggio: {details}"))
        tags = equipment_tags(listing)
        if tags:
            lines.append(s.dim(f"     Dotazione: {', '.join(tags)}"))
        if valuation.notes:
            lines.append(s.yellow(f"     Nota: {'; '.join(valuation.notes)}"))
        lines.append(s.dim(f"     {listing.url}"))

    # --- classifica ---
    lines.append("")
    lines.append(s.cyan("─" * 78))
    lines.append(s.bold("  CLASSIFICA COMPLETA"))
    lines.append(s.cyan("─" * 78))
    header = (f"  {'#':>2}  {'Pt':>5}  {'Anno':>4}  {'Allestimento':<22}  "
              f"{'Km':>9}  {'Prezzo':>10}  {'Conven.':>8}")
    lines.append(s.dim(header))
    for position, valuation in enumerate(ranked[: cfg.output.table_rows], start=1):
        listing = valuation.listing
        trim = describe_trim(listing)[:22]
        advantage = pct(valuation.advantage_pct)
        colored = (s.green if valuation.advantage_pct >= 0 else s.red)(f"{advantage:>8}")
        lines.append(f"  {position:>2}  {num(valuation.score):>5}  "
                     f"{listing.year or '?':>4}  {trim:<22}  "
                     f"{fmt_int(listing.odometer_km):>9}  {eur(valuation.price):>10}  {colored}")

    if skipped:
        lines.append("")
        lines.append(s.yellow(
            "  " + plural(len(skipped), "annuncio escluso", "annunci esclusi")
            + " dalla valutazione:"))
        for valuation in skipped[:5]:
            lines.append(s.dim(f"    {valuation.listing.vin} — {'; '.join(valuation.notes)}"))

    lines.append("")
    lines.append(s.dim(f"  Fonte: {page_url(cfg.search)}"))
    calibration = meta.get("calibration")
    if calibration and abs(calibration - 1.0) > 0.001:
        lines.append(s.dim(f"  Stime ricalibrate sull'inventario corrente "
                           f"(fattore {num(calibration, 3)}): la convenienza è relativa "
                           "alle altre auto in vendita adesso."))
    lines.append(s.dim(f"  Backend: {meta.get('backend', 'n.d.')} · "
                       "valore stimato da un modello di deprezzamento, non da una perizia."))
    lines.append("")
    return "\n".join(lines)


# --- markdown ----------------------------------------------------------------

def format_markdown(cfg: Config, ranked: list[Valuation], skipped: list[Valuation],
                    changes: Changes, meta: dict[str, Any]) -> str:
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    out: list[str] = []
    out.append("# Tesla usate — miglior rapporto qualità/prezzo")
    out.append("")
    out.append(f"*Aggiornato il {now} · {search_description(cfg)}*")
    out.append("")

    if not ranked:
        out.append("Nessuna auto valutabile con i filtri attuali.")
        return "\n".join(out)

    best = ranked[0]
    out.append("## In sintesi")
    out.append("")
    out.append(f"**{best.listing.label}** a **{eur(best.price)}** — "
               f"punteggio **{num(best.score)}/100**, "
               f"convenienza **{pct(best.advantage_pct)}** rispetto a un valore stimato di "
               f"{eur(best.estimated_value)}.")
    out.append("")
    out.append(f"- Auto in inventario: **{len(ranked) + len(skipped)}**")
    out.append(f"- Prezzo piu basso: **{eur(min(v.price for v in ranked))}**")
    out.append(f"- Nuovi annunci dall'ultimo controllo: **{len(changes.new_listings)}**")
    out.append(f"- Ribassi: **{len(changes.price_drops)}** · "
               f"Auto sparite: **{len(changes.removed)}**")
    out.append("")

    if changes.has_news:
        out.append("## Novita dall'ultimo controllo")
        out.append("")
        for valuation in changes.new_listings:
            out.append(f"- \U0001F195 **Nuovo** — [{valuation.listing.label}]({valuation.listing.url}) "
                       f"a {eur(valuation.price)} (punteggio {num(valuation.score)})")
        for change in changes.price_drops:
            out.append(f"- ↓ **Ribasso** — [{change.valuation.listing.label}]"
                       f"({change.valuation.listing.url}): {eur(change.old_price)} → "
                       f"{eur(change.new_price)} ({pct(change.delta_pct)})")
        for change in changes.price_rises:
            out.append(f"- ↑ **Rincaro** — [{change.valuation.listing.label}]"
                       f"({change.valuation.listing.url}): {eur(change.old_price)} → "
                       f"{eur(change.new_price)} ({pct(change.delta_pct)})")
        for gone in changes.removed:
            out.append(f"- − **Non piu disponibile** — {gone.get('label', gone.get('vin'))} "
                       f"(era a {eur(gone.get('price'))})")
        out.append("")

    out.append("## Le migliori occasioni")
    out.append("")
    for position, valuation in enumerate(ranked[: cfg.output.top_n], start=1):
        listing = valuation.listing
        out.append(f"### {position}. {listing.label} — {num(valuation.score)}/100")
        out.append("")
        out.append(f"- **Prezzo**: {eur(valuation.price)}"
                   + (f" (oneri inclusi: {eur(listing.fees)})" if listing.fees else ""))
        out.append(f"- **Valore stimato**: {eur(valuation.estimated_value)} → "
                   f"convenienza {eur(valuation.advantage_eur)} ({pct(valuation.advantage_pct)})")
        out.append(f"- **Percorrenza**: {km(listing.odometer_km)}")
        if listing.range_km:
            out.append(f"- **Autonomia WLTP**: {fmt_int(listing.range_km)} km")
        tags = equipment_tags(listing)
        if tags:
            out.append(f"- **Dotazione**: {', '.join(tags)}")
        parts = " · ".join(f"{k} {num(v, 0)}" for k, v in valuation.score_parts.items())
        out.append(f"- **Punteggio**: {parts}")
        if valuation.notes:
            out.append(f"- *Nota: {'; '.join(valuation.notes)}*")
        out.append(f"- [Vedi l'annuncio]({listing.url}) · VIN `{listing.vin}`")
        out.append("")

    out.append("## Classifica completa")
    out.append("")
    out.append("| # | Punteggio | Anno | Allestimento | Km | Prezzo | Valore stimato | Convenienza |")
    out.append("|--:|--:|--:|---|--:|--:|--:|--:|")
    for position, valuation in enumerate(ranked[: cfg.output.table_rows], start=1):
        listing = valuation.listing
        out.append(f"| {position} | {num(valuation.score)} | {listing.year or '?'} | "
                   f"[{describe_trim(listing)}]({listing.url}) | {fmt_int(listing.odometer_km)} | "
                   f"{eur(valuation.price)} | {eur(valuation.estimated_value)} | "
                   f"{pct(valuation.advantage_pct)} |")
    out.append("")
    out.append("---")
    out.append("")
    calibration = meta.get("calibration")
    if calibration and abs(calibration - 1.0) > 0.001:
        out.append(f"Le stime sono ricalibrate sull'inventario corrente "
                   f"(fattore {num(calibration, 3)}): la convenienza misura quanto un'auto "
                   "costa meno del previsto rispetto alle altre in vendita adesso.")
        out.append("")
    out.append(f"Fonte: [inventario Tesla]({page_url(cfg.search)}) · "
               f"backend `{meta.get('backend', 'n.d.')}`. "
               "Il valore stimato viene da un modello di deprezzamento configurabile: "
               "e un riferimento, non una perizia.")
    return "\n".join(out)


# --- notifica breve ----------------------------------------------------------

def format_notification(cfg: Config, ranked: list[Valuation], changes: Changes) -> str:
    if not ranked:
        return "Tesla: nessuna auto valutabile con i filtri attuali."
    best = ranked[0]
    lines = [f"\U0001F697 Tesla {search_description(cfg)}",
             "",
             f"Migliore: {best.listing.label}",
             f"{eur(best.price)} · {km(best.listing.odometer_km)} · "
             f"punteggio {num(best.score)}/100",
             f"Convenienza {pct(best.advantage_pct)} su un valore stimato di {eur(best.estimated_value)}",
             best.listing.url]
    if changes.new_listings:
        lines += ["", f"Nuovi annunci: {len(changes.new_listings)}"]
        for valuation in changes.new_listings[:3]:
            lines.append(f"  • {valuation.listing.label} — {eur(valuation.price)}")
    if changes.price_drops:
        lines += ["", f"Ribassi: {len(changes.price_drops)}"]
        for change in changes.price_drops[:3]:
            lines.append(f"  • {change.valuation.listing.label}: "
                         f"{eur(change.old_price)} → {eur(change.new_price)} "
                         f"({pct(change.delta_pct)})")
    return "\n".join(lines)
