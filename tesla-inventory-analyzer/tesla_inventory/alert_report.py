"""Come viene presentata un'occasione: oggetto, testo ed HTML dell'email.

L'HTML e volutamente antiquato - tabelle e stili in linea - perche i client di
posta, Outlook in testa, ignorano i fogli di stile e le tecniche di impaginazione
moderne.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any

from .alerts import Opportunity, describe_thresholds, evaluate_thresholds
from .config import Config
from .fetch import page_url
from .parse import COLOR_LABELS
from .report import equipment_tags, eur, fmt_int, km, num, pct
from .valuation import describe_generation, describe_trim


def _color_label(listing) -> str:
    if listing.color_name:
        return listing.color_name
    if listing.color:
        return COLOR_LABELS.get(listing.color, listing.color).capitalize()
    return "colore non dichiarato"


def subject(opportunities: list[Opportunity]) -> str:
    """Oggetto dell'email: deve dire tutto anche letto dalla notifica del telefono."""
    if not opportunities:
        return "Nessuna occasione"
    best = opportunities[0].valuation
    if len(opportunities) == 1:
        return (f"Occasione: {best.listing.label} a {eur(best.price)} "
                f"({pct(best.advantage_pct, signed=False)} sotto il valore stimato)")
    return (f"{len(opportunities)} occasioni — la migliore: "
            f"{best.listing.label} a {eur(best.price)}")


def text_body(cfg: Config, opportunities: list[Opportunity], meta: dict[str, Any]) -> str:
    now = datetime.now().strftime("%d/%m/%Y alle %H:%M")
    lines: list[str] = []
    lines.append(f"Controllo del {now}")
    lines.append("")
    una = len(opportunities) == 1
    lines.append(f"Ho trovato {len(opportunities)} "
                 f"{'occasione che supera' if una else 'occasioni che superano'} "
                 f"le soglie che hai impostato ({describe_thresholds(cfg.alert)}).")
    lines.append("")

    for position, opportunity in enumerate(opportunities, start=1):
        valuation = opportunity.valuation
        listing = valuation.listing
        lines.append("-" * 62)
        lines.append(f"{position}. {listing.label} — {_color_label(listing)}")
        lines.append("")
        lines.append(f"   Prezzo          {eur(valuation.price)}")
        lines.append(f"   Valore stimato  {eur(valuation.estimated_value)}")
        lines.append(f"   Convenienza     {eur(valuation.advantage_eur)} "
                     f"({pct(valuation.advantage_pct)})")
        lines.append(f"   Punteggio       {num(valuation.score)}/100")
        lines.append(f"   Percorrenza     {km(listing.odometer_km)}")
        generazione = describe_generation(listing)
        if generazione:
            lines.append(f"   Generazione     {generazione}")
        if listing.range_km:
            lines.append(f"   Autonomia       {fmt_int(listing.range_km)} km WLTP")
        tags = equipment_tags(listing)
        if tags:
            lines.append(f"   Dotazione       {', '.join(tags)}")
        if opportunity.first_time:
            lines.append("   Stato           mai segnalata prima")
        elif opportunity.drop_since_notified is not None:
            lines.append(f"   Stato           già segnalata, ora costa "
                         f"{eur(abs(opportunity.drop_since_notified))} in meno")
        lines.append("")
        lines.append(f"   Perché: {'; '.join(opportunity.reasons)}")
        lines.append("")
        lines.append(f"   {listing.url}")
        lines.append("")

    lines.append("-" * 62)
    lines.append("")
    lines.append("Il valore stimato viene da un modello di deprezzamento calibrato "
                 "sull'inventario Tesla del momento: è un riferimento per capire se il "
                 "prezzo è fuori mercato, non una perizia. Prima di muoverti, apri "
                 "l'annuncio e verifica storia del veicolo e condizioni di garanzia.")
    lines.append("")
    lines.append(f"Inventario completo: {page_url(cfg.search)}")
    return "\n".join(lines)


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _row(label: str, value: str, strong: bool = False) -> str:
    weight = "600" if strong else "400"
    return (f'<tr>'
            f'<td style="padding:4px 12px 4px 0;color:#52514e;font-size:13px;'
            f'white-space:nowrap;">{_esc(label)}</td>'
            f'<td style="padding:4px 0;color:#0b0b0b;font-size:14px;'
            f'font-weight:{weight};">{_esc(value)}</td>'
            f'</tr>')


def html_body(cfg: Config, opportunities: list[Opportunity], meta: dict[str, Any]) -> str:
    now = datetime.now().strftime("%d/%m/%Y alle %H:%M")
    cards: list[str] = []

    for opportunity in opportunities:
        valuation = opportunity.valuation
        listing = valuation.listing
        rows = [
            _row("Prezzo", eur(valuation.price), strong=True),
            _row("Valore stimato", eur(valuation.estimated_value)),
            _row("Convenienza", f"{eur(valuation.advantage_eur)} ({pct(valuation.advantage_pct)})",
                 strong=True),
            _row("Punteggio", f"{num(valuation.score)}/100"),
            _row("Percorrenza", km(listing.odometer_km)),
            _row("Colore", _color_label(listing)),
        ]
        generazione = describe_generation(listing)
        if generazione:
            rows.append(_row("Generazione", generazione))
        if listing.range_km:
            rows.append(_row("Autonomia", f"{fmt_int(listing.range_km)} km WLTP"))
        tags = equipment_tags(listing)
        if tags:
            rows.append(_row("Dotazione", ", ".join(tags)))
        if opportunity.first_time:
            stato = "Mai segnalata prima"
        elif opportunity.drop_since_notified is not None:
            stato = f"Già segnalata, ora costa {eur(abs(opportunity.drop_since_notified))} in meno"
        else:
            stato = "Già segnalata"
        rows.append(_row("Stato", stato))

        cards.append(f'''
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
             style="border:1px solid #e3e2de;border-radius:10px;margin:0 0 16px;background:#ffffff;">
        <tr><td style="padding:18px 20px;">
          <div style="font-size:17px;font-weight:650;color:#0b0b0b;margin-bottom:2px;">
            {_esc(listing.year or "")} Model 3 {_esc(describe_trim(listing))}
          </div>
          <div style="font-size:13px;color:#52514e;margin-bottom:14px;">
            {_esc(_color_label(listing))} &middot; VIN {_esc(listing.vin)}
          </div>
          <table role="presentation" cellpadding="0" cellspacing="0">{''.join(rows)}</table>
          <div style="margin-top:14px;padding:10px 12px;background:#f3faf3;
                      border-left:3px solid #0ca30c;font-size:13px;color:#2c4a2c;">
            <strong>Perché te la segnalo:</strong> {_esc("; ".join(opportunity.reasons))}
          </div>
          <div style="margin-top:16px;">
            <a href="{_esc(listing.url)}"
               style="display:inline-block;background:#2a78d6;color:#ffffff;
                      text-decoration:none;padding:10px 18px;border-radius:6px;
                      font-size:14px;font-weight:600;">Apri l'annuncio su Tesla</a>
          </div>
        </td></tr>
      </table>''')

    plural = "occasione da valutare" if len(opportunities) == 1 else "occasioni da valutare"
    return f'''<!DOCTYPE html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f4f3f0;
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f3f0;">
    <tr><td align="center" style="padding:24px 12px;">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0"
             style="max-width:600px;width:100%;">
        <tr><td style="padding:0 0 18px;">
          <div style="font-size:20px;font-weight:680;color:#0b0b0b;">
            {len(opportunities)} {plural}
          </div>
          <div style="font-size:13px;color:#52514e;margin-top:4px;">
            Controllo del {_esc(now)} &middot; soglie: {_esc(describe_thresholds(cfg.alert))}
          </div>
        </td></tr>
        <tr><td>{''.join(cards)}</td></tr>
        <tr><td style="padding:8px 4px 0;font-size:12px;color:#6f6e6a;line-height:1.55;">
          <p style="margin:0 0 10px;">Il valore stimato viene da un modello di deprezzamento
          calibrato sull'inventario Tesla del momento: serve a capire se il prezzo è fuori
          mercato, non è una perizia. Prima di muoverti apri l'annuncio e verifica storia del
          veicolo e condizioni di garanzia.</p>
          <p style="margin:0;"><a href="{_esc(page_url(cfg.search))}"
             style="color:#2a78d6;">Vedi tutto l'inventario Tesla</a></p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>'''


# --- verdetto in console -----------------------------------------------------

def format_console_verdict(cfg: Config, result, color: bool = True) -> str:
    """Il verdetto in testa all'output: occasione trovata oppure no.

    Serve a rispondere a colpo d'occhio alla domanda "devo muovermi?", senza
    dover leggere la classifica.
    """
    def paint(code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if color else text

    lines: list[str] = []
    rule = "═" * 78
    criteri = describe_thresholds(cfg.alert)

    if not result.ranked:
        lines.append(paint("33", "  NESSUNA AUTO corrisponde ai criteri di ricerca."))
        lines.append(f"  Valutate {result.baseline_count} auto dell'inventario, "
                     "nessuna con le caratteristiche che cerchi.")
        return "\n".join(lines)

    if result.qualifying:
        lines.append(paint("1;32", rule))
        plural = "OCCASIONE TROVATA" if len(result.qualifying) == 1 else "OCCASIONI TROVATE"
        lines.append(paint("1;32", f"  {len(result.qualifying)} {plural}"))
        lines.append(paint("1;32", rule))
        for valuation in result.qualifying:
            listing = valuation.listing
            lines.append("")
            lines.append(paint("1", f"  {listing.label} — {_color_label(listing)}"))
            lines.append(f"     {eur(valuation.price)} · {km(listing.odometer_km)} · "
                         f"punteggio {num(valuation.score)}/100")
            lines.append(paint("32", f"     {eur(valuation.advantage_eur)} sotto il valore "
                                     f"stimato ({pct(valuation.advantage_pct, signed=False)})"))
            lines.append(f"     {listing.url}")
        if result.opportunities:
            quante = len(result.opportunities)
            azione = ("email inviata" if result.email_sent
                      else ("nuova rispetto all'ultimo avviso" if quante == 1
                            else "nuove rispetto all'ultimo avviso"))
            lines.append("")
            lines.append(paint("1;32", f"  {quante} da segnalare — {azione}."))
        else:
            lines.append("")
            lines.append(paint("2", "  Già segnalate in precedenza: nessun nuovo avviso."))
        return "\n".join(lines)

    # Nessuna occasione: diciamo quanto ci e mancato, non solo "no".
    best = result.ranked[0]
    _, motivi = evaluate_thresholds(cfg.alert, best)
    lines.append(paint("2", rule))
    lines.append(paint("1", "  NESSUNA OCCASIONE in questo momento"))
    lines.append(paint("2", rule))
    lines.append(f"  {len(result.ranked)} auto corrispondono ai tuoi criteri, "
                 f"nessuna supera le soglie ({criteri}).")
    lines.append("")
    lines.append(f"  La più vicina: {best.listing.label} a {eur(best.price)} — "
                 f"punteggio {num(best.score)}, convenienza {pct(best.advantage_pct)}")
    lines.append(paint("2", f"  Le manca: {'; '.join(motivi)}"))
    return "\n".join(lines)
