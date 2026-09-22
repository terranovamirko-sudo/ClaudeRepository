"""Report HTML autonomo: una pagina sola, senza risorse esterne.

Impostazione grafica: numero-eroe per il punteggio migliore, riga di indicatori
sintetici, tabella per la classifica. Gli stati (ribasso/rincaro) sono sempre
resi da icona + etichetta oltre che dal colore, cosi restano leggibili anche
senza percezione del colore.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any

from .config import Config
from .fetch import page_url
from .history import Changes
from .report import (equipment_tags, eur, fmt_int, km, num, pct, search_description)
from .valuation import Valuation, describe_generation, describe_trim

CSS = """
:root {
  color-scheme: light;
  --page:           #f4f3f0;
  --surface-1:      #fcfcfb;
  --text-primary:   #0b0b0b;
  --text-secondary: #52514e;
  --text-muted:     #6f6e6a;
  --border:         rgba(0,0,0,0.10);
  --accent:         #2a78d6;
  --good:           #0ca30c;
  --warning:        #fab219;
  --critical:       #d03b3b;
  --good-soft:      rgba(12,163,12,0.10);
  --critical-soft:  rgba(208,59,59,0.10);
  --accent-soft:    rgba(42,120,214,0.10);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --page:           #121211;
    --surface-1:      #1a1a19;
    --text-primary:   #ffffff;
    --text-secondary: #c3c2b7;
    --text-muted:     #9a998f;
    --border:         rgba(255,255,255,0.12);
    --accent:         #3987e5;
    --good-soft:      rgba(12,163,12,0.16);
    --critical-soft:  rgba(208,59,59,0.16);
    --accent-soft:    rgba(57,135,229,0.16);
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page:           #121211;
  --surface-1:      #1a1a19;
  --text-primary:   #ffffff;
  --text-secondary: #c3c2b7;
  --text-muted:     #9a998f;
  --border:         rgba(255,255,255,0.12);
  --accent:         #3987e5;
  --good-soft:      rgba(12,163,12,0.16);
  --critical-soft:  rgba(208,59,59,0.16);
  --accent-soft:    rgba(57,135,229,0.16);
}

* { box-sizing: border-box; }
body {
  margin: 0; padding: 32px 16px 64px;
  background: var(--page); color: var(--text-primary);
  font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
.wrap { max-width: 1040px; margin: 0 auto; }
h1 { font-size: 1.6rem; margin: 0 0 4px; letter-spacing: -0.01em; }
h2 { font-size: 1.15rem; margin: 40px 0 14px; letter-spacing: -0.01em; }
.sub { color: var(--text-secondary); margin: 0 0 28px; font-size: 0.92rem; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

.card {
  background: var(--surface-1); border: 1px solid var(--border);
  border-radius: 14px; padding: 20px;
}

/* Riga di indicatori sintetici */
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }
.kpi { background: var(--surface-1); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; }
.kpi .label { font-size: 0.74rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-muted); }
.kpi .value { font-size: 1.5rem; font-weight: 650; margin-top: 4px; font-variant-numeric: tabular-nums; }
.kpi .note { font-size: 0.8rem; color: var(--text-secondary); margin-top: 2px; }

/* Auto migliore */
.hero { display: flex; flex-wrap: wrap; gap: 24px; align-items: flex-start; margin-top: 12px; }
.hero .figure { min-width: 132px; }
.hero .figure .n { font-size: 3.4rem; line-height: 1; font-weight: 680; font-variant-numeric: tabular-nums; }
.hero .figure .of { color: var(--text-muted); font-size: 0.85rem; }
.hero .body { flex: 1 1 320px; }
.hero .name { font-size: 1.25rem; font-weight: 640; margin-bottom: 6px; }
.meter { height: 8px; border-radius: 4px; background: var(--border); overflow: hidden; margin: 10px 0 14px; max-width: 320px; }
.meter > span { display: block; height: 100%; border-radius: 4px; background: var(--accent); }

.facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px 20px; margin-top: 12px; }
.fact .k { font-size: 0.74rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); }
.fact .v { font-weight: 600; font-variant-numeric: tabular-nums; }

.tags { margin-top: 14px; display: flex; flex-wrap: wrap; gap: 6px; }
.tag { font-size: 0.78rem; padding: 3px 9px; border-radius: 999px;
       background: var(--accent-soft); border: 1px solid var(--border); color: var(--text-secondary); }
.note { margin-top: 12px; font-size: 0.84rem; color: var(--text-secondary);
        border-left: 3px solid var(--warning); padding-left: 10px; }

/* Stati: colore + icona + etichetta, mai colore da solo */
.chip { display: inline-flex; align-items: center; gap: 6px; font-size: 0.8rem; font-weight: 600;
        padding: 2px 9px; border-radius: 999px; border: 1px solid var(--border); }
.chip.good { background: var(--good-soft); border-color: var(--good); }
.chip.bad  { background: var(--critical-soft); border-color: var(--critical); }
.chip.neutral { background: var(--accent-soft); border-color: var(--accent); }

.changes { list-style: none; padding: 0; margin: 0; }
.changes li { padding: 10px 0; border-bottom: 1px solid var(--border);
              display: flex; flex-wrap: wrap; gap: 10px; align-items: baseline; }
.changes li:last-child { border-bottom: 0; }
.changes .detail { color: var(--text-secondary); font-size: 0.9rem; font-variant-numeric: tabular-nums; }

table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
th, td { padding: 9px 10px; text-align: right; border-bottom: 1px solid var(--border); font-size: 0.9rem; }
th { color: var(--text-muted); font-weight: 600; font-size: 0.74rem;
     text-transform: uppercase; letter-spacing: 0.05em; white-space: nowrap; }
th:nth-child(2), td:nth-child(2), th:nth-child(4), td:nth-child(4) { text-align: left; }
tbody tr:hover { background: var(--accent-soft); }
td.rank { color: var(--text-muted); }

details { margin-top: 32px; }
summary { cursor: pointer; color: var(--text-secondary); font-size: 0.9rem; }
.method { font-size: 0.88rem; color: var(--text-secondary); }
.method code { background: var(--accent-soft); padding: 1px 5px; border-radius: 4px; }
footer { margin-top: 40px; color: var(--text-muted); font-size: 0.82rem; }

@media (max-width: 640px) {
  body { padding: 20px 16px 48px; }
  .hero .figure .n { font-size: 2.8rem; }
  th, td { padding: 8px 6px; font-size: 0.84rem; }
  .hide-sm { display: none; }
}
"""


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _chip(kind: str, icon: str, label: str) -> str:
    return f'<span class="chip {kind}"><span aria-hidden="true">{icon}</span>{_esc(label)}</span>'


def _kpi(label: str, value: str, note: str = "") -> str:
    note_html = f'<div class="note">{_esc(note)}</div>' if note else ""
    return (f'<div class="kpi"><div class="label">{_esc(label)}</div>'
            f'<div class="value">{_esc(value)}</div>{note_html}</div>')


def _fact(key: str, value: str) -> str:
    return f'<div class="fact"><div class="k">{_esc(key)}</div><div class="v">{_esc(value)}</div></div>'


def _hero(valuation: Valuation) -> str:
    listing = valuation.listing
    facts = [
        _fact("Prezzo", eur(valuation.price)),
        _fact("Valore stimato", eur(valuation.estimated_value)),
        _fact("Convenienza", f"{eur(valuation.advantage_eur)} ({pct(valuation.advantage_pct)})"),
        _fact("Percorrenza", km(listing.odometer_km)),
    ]
    if listing.range_km:
        facts.append(_fact("Autonomia WLTP", f"{fmt_int(listing.range_km)} km"))
    if listing.year:
        facts.append(_fact("Anno", str(listing.year)))
    generazione = describe_generation(listing)
    if generazione:
        facts.append(_fact("Generazione", generazione))

    tags = "".join(f'<span class="tag">{_esc(t)}</span>' for t in equipment_tags(listing))
    tags_html = f'<div class="tags">{tags}</div>' if tags else ""
    note_html = (f'<div class="note">{_esc("; ".join(valuation.notes))}</div>'
                 if valuation.notes else "")
    parts = " · ".join(f"{k} {num(v, 0)}" for k, v in valuation.score_parts.items())

    return f"""
    <div class="card hero">
      <div class="figure">
        <div class="n">{_esc(num(valuation.score))}</div>
        <div class="of">punteggio su 100</div>
        <div class="meter"><span style="width:{max(0.0, min(100.0, valuation.score)):.0f}%"></span></div>
      </div>
      <div class="body">
        <div class="name"><a href="{_esc(listing.url)}">{_esc(listing.label)}</a></div>
        <div class="detail" style="color:var(--text-secondary);font-size:0.9rem">
          {_esc(parts)}
        </div>
        <div class="facts">{''.join(facts)}</div>
        {tags_html}
        {note_html}
      </div>
    </div>"""


def _changes_section(changes: Changes) -> str:
    if changes.first_run:
        return ('<p class="sub">Primo controllo registrato: dai prossimi giri '
                'compaiono qui nuovi annunci, ribassi e auto non piu disponibili.</p>')
    if not changes.has_news:
        return ('<p class="sub">Nessuna variazione dall\'ultimo controllo.</p>')

    items: list[str] = []
    for valuation in changes.new_listings:
        items.append(
            f'<li>{_chip("neutral", "➕", "Nuovo")}'
            f'<a href="{_esc(valuation.listing.url)}">{_esc(valuation.listing.label)}</a>'
            f'<span class="detail">{_esc(eur(valuation.price))} · '
            f'punteggio {_esc(num(valuation.score))}</span></li>')
    for change in changes.price_drops:
        items.append(
            f'<li>{_chip("good", "↓", "Ribasso")}'
            f'<a href="{_esc(change.valuation.listing.url)}">'
            f'{_esc(change.valuation.listing.label)}</a>'
            f'<span class="detail">{_esc(eur(change.old_price))} → '
            f'{_esc(eur(change.new_price))} ({_esc(pct(change.delta_pct))})</span></li>')
    for change in changes.price_rises:
        items.append(
            f'<li>{_chip("bad", "↑", "Rincaro")}'
            f'<a href="{_esc(change.valuation.listing.url)}">'
            f'{_esc(change.valuation.listing.label)}</a>'
            f'<span class="detail">{_esc(eur(change.old_price))} → '
            f'{_esc(eur(change.new_price))} ({_esc(pct(change.delta_pct))})</span></li>')
    for gone in changes.removed:
        items.append(
            f'<li>{_chip("neutral", "✖", "Non disponibile")}'
            f'<span>{_esc(gone.get("label", gone.get("vin")))}</span>'
            f'<span class="detail">era a {_esc(eur(gone.get("price")))}</span></li>')
    return f'<ul class="changes card">{"".join(items)}</ul>'


def _table(cfg: Config, ranked: list[Valuation]) -> str:
    rows: list[str] = []
    for position, valuation in enumerate(ranked[: cfg.output.table_rows], start=1):
        listing = valuation.listing
        good = valuation.advantage_pct >= 0
        chip = _chip("good" if good else "bad", "↓" if good else "↑",
                     pct(valuation.advantage_pct))
        rows.append(
            f"<tr>"
            f'<td class="rank">{position}</td>'
            f'<td><a href="{_esc(listing.url)}">{_esc(listing.year or "?")} '
            f'{_esc(describe_trim(listing))}</a></td>'
            f"<td>{_esc(num(valuation.score))}</td>"
            f'<td class="hide-sm">{_esc(km(listing.odometer_km))}</td>'
            f"<td>{_esc(eur(valuation.price))}</td>"
            f'<td class="hide-sm">{_esc(eur(valuation.estimated_value))}</td>'
            f"<td>{chip}</td>"
            f"</tr>")
    return f"""
    <div class="card" style="padding:4px 12px">
      <table>
        <thead><tr>
          <th>#</th><th>Auto</th><th>Punteggio</th>
          <th class="hide-sm">Percorrenza</th><th>Prezzo</th>
          <th class="hide-sm">Valore stimato</th><th>Convenienza</th>
        </tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>"""


def format_html(cfg: Config, ranked: list[Valuation], skipped: list[Valuation],
                changes: Changes, meta: dict[str, Any]) -> str:
    now = datetime.now().strftime("%d/%m/%Y alle %H:%M")
    source = page_url(cfg.search)
    calibration = meta.get("calibration")
    calibration_text = (f" (fattore applicato: {num(calibration, 3)})"
                        if calibration and abs(calibration - 1.0) > 0.001 else "")

    if not ranked:
        body = ('<div class="card"><p>Nessuna auto valutabile con i filtri attuali.</p></div>')
        kpis = ""
        hero = ""
        table = ""
    else:
        best = ranked[0]
        cheapest = min(ranked, key=lambda v: v.price)
        kpis = (
            '<div class="kpis">'
            + _kpi("Auto in inventario", str(len(ranked) + len(skipped)),
                   f"{len(ranked)} valutate")
            + _kpi("Prezzo piu basso", eur(cheapest.price),
                   cheapest.listing.label)
            + _kpi("Miglior convenienza", pct(max(v.advantage_pct for v in ranked)),
                   "rispetto al valore stimato")
            + _kpi("Novita", str(len(changes.new_listings) + len(changes.price_drops)),
                   f"{len(changes.new_listings)} nuove · {len(changes.price_drops)} ribassi")
            + "</div>")
        hero = _hero(best)
        table = _table(cfg, ranked)
        body = ""

    others = "".join(
        f"""
    <div class="card" style="margin-top:12px">
      <div class="name" style="font-weight:600">{position}. <a href="{_esc(v.listing.url)}">
        {_esc(v.listing.label)}</a> — {_esc(num(v.score))}/100</div>
      <div class="facts">
        {_fact("Prezzo", eur(v.price))}
        {_fact("Valore stimato", eur(v.estimated_value))}
        {_fact("Convenienza", pct(v.advantage_pct))}
        {_fact("Percorrenza", km(v.listing.odometer_km))}
      </div>
    </div>"""
        for position, v in enumerate(ranked[1: cfg.output.top_n], start=2))

    return f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tesla usate — rapporto qualita/prezzo</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <h1>Tesla usate — miglior rapporto qualita/prezzo</h1>
  <p class="sub">Aggiornato il {_esc(now)} · {_esc(search_description(cfg))} ·
     <a href="{_esc(source)}">apri l'inventario Tesla</a></p>

  {kpis}
  {body}

  {'<h2>La scelta migliore</h2>' + hero if hero else ''}
  {'<h2>Le alternative</h2>' + others if others else ''}

  <h2>Novita dall'ultimo controllo</h2>
  {_changes_section(changes)}

  {'<h2>Classifica completa</h2>' + table if table else ''}

  <details>
    <summary>Come viene calcolato il punteggio</summary>
    <div class="method card" style="margin-top:12px">
      <p>Il <strong>valore stimato</strong> parte dal listino del nuovo per allestimento e
      anno, applica una curva di deprezzamento
      (<code>{cfg.valuation.first_year_retention:.2f}</code> il primo anno, poi
      <code>{cfg.valuation.annual_retention:.2f}</code> l'anno), corregge per la percorrenza
      rispetto ai <code>{cfg.valuation.expected_km_per_year:,}</code> km/anno attesi
      (<code>{cfg.valuation.cost_per_excess_km}</code> &euro;/km), somma il valore residuo
      degli optional e della garanzia batteria ancora disponibile, e sottrae una penalita
      in caso di danni dichiarati.</p>
      <p>La <strong>convenienza</strong> e la differenza percentuale fra valore stimato e
      prezzo richiesto. Il <strong>punteggio</strong> combina convenienza
      ({cfg.score.weight_deal:.0%}), percorrenza ({cfg.score.weight_mileage:.0%}),
      garanzia residua ({cfg.score.weight_warranty:.0%}) e dotazione
      ({cfg.score.weight_equipment:.0%}).</p>
      <p>Le stime vengono poi <strong>ricalibrate sull'inventario osservato</strong>, in modo
      che l'auto mediana risulti a convenienza zero{calibration_text}. Cosi la convenienza
      dice quanto un'auto costa meno di quello che ci si aspetterebbe viste le altre in
      vendita nello stesso momento, senza dipendere dall'esattezza dei listini.</p>
      <p>Tutti i parametri stanno nel file di configurazione: i listini di riferimento sono
      stime e vanno ritoccati se cambiano i prezzi Tesla.</p>
    </div>
  </details>

  <footer>
    Dati letti dall'inventario Tesla (backend <code>{_esc(meta.get('backend', 'n.d.'))}</code>).
    Il valore stimato e il risultato di un modello statistico configurabile, non di una perizia:
    prima di acquistare, verifica sempre l'annuncio originale.
  </footer>
</div>
</body>
</html>
"""
