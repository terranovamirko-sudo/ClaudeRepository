"""Test della catena completa: parsing, valutazione, storico, report."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tesla_inventory import html_report, report
from tesla_inventory.app import apply_filters, run_once
from tesla_inventory.config import Config
from tesla_inventory.fetch import _looks_like_challenge, build_query, extract_results, page_url
from tesla_inventory.history import HistoryStore
from tesla_inventory.parse import (TRIM_LR, TRIM_PERF, TRIM_RWD, parse_all,
                                   parse_listing, to_float, to_int)
from tesla_inventory.valuation import (evaluate, evaluate_all, rank,
                                       retention_factor, warranty_remaining)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class TestNumberParsing(unittest.TestCase):
    def test_formati_italiani_e_inglesi(self):
        self.assertEqual(to_float("45.000,50"), 45000.5)
        self.assertEqual(to_float("45,000.50"), 45000.5)
        self.assertEqual(to_float("€ 29.900"), 29900.0)
        self.assertEqual(to_float("1.234.567"), 1234567.0)

    def test_decimali_brevi_restano_decimali(self):
        self.assertEqual(to_float("5.6"), 5.6)
        self.assertEqual(to_float("3,3"), 3.3)

    def test_valori_non_numerici(self):
        for value in (None, "", "n.d.", [], {}, True):
            self.assertIsNone(to_float(value), msg=repr(value))

    def test_to_int_arrotonda(self):
        self.assertEqual(to_int("123.456"), 123456)
        self.assertEqual(to_int(12.6), 13)


class TestParsing(unittest.TestCase):
    def setUp(self):
        self.records = load_fixture("inventario_esempio.json")["results"]
        self.listings = parse_all(self.records, zip_code="90126")

    def test_tutti_gli_annunci_con_vin_sono_letti(self):
        self.assertEqual(len(self.listings), len(self.records))

    def test_allestimenti_riconosciuti_senza_indovinare(self):
        trims = {l.vin: l.trim for l in self.listings}
        self.assertEqual(trims["LRW3E7EK3MC100003"], TRIM_LR)
        self.assertEqual(trims["LRW3E7EC4MC100004"], TRIM_PERF)
        self.assertEqual(trims["LRW3E7EA1KC100001"], TRIM_RWD)
        self.assertFalse(any(l.trim_guessed for l in self.listings))

    def test_optional_riconosciuti(self):
        by_vin = {l.vin: l for l in self.listings}
        fsd = by_vin["LRW3E7EK6NC100006"]
        self.assertTrue(fsd.has_fsd)
        self.assertFalse(fsd.has_eap, "FSD non deve sommarsi all'Autopilot avanzato")
        eap = by_vin["LRW3E7EK3MC100003"]
        self.assertTrue(eap.has_eap)
        self.assertTrue(eap.has_tow_hitch)
        self.assertTrue(eap.has_upgraded_wheels)
        self.assertTrue(by_vin["LRW3E7EC4MC100004"].has_premium_paint)
        self.assertTrue(by_vin["LRW3E7EC4MC100004"].has_white_interior)

    def test_cerchi_base_non_contati_come_maggiorati(self):
        base = next(l for l in self.listings if l.vin == "LRW3E7EA5NC100005")
        self.assertFalse(base.has_upgraded_wheels)

    def test_specifiche_tecniche(self):
        lr = next(l for l in self.listings if l.vin == "LRW3E7EK3MC100003")
        self.assertEqual(lr.range_km, 602.0)   # Long Range pre-restyling
        self.assertEqual(lr.acceleration_s, 4.4)

    def test_oneri_sommati_al_prezzo(self):
        con_oneri = next(l for l in self.listings if l.vin == "LRW3E7EK8RC100008")
        self.assertEqual(con_oneri.fees, 300.0)
        self.assertEqual(con_oneri.effective_price, con_oneri.price + 300.0)

    def test_miglia_convertite_in_chilometri(self):
        listing = parse_listing({"VIN": "TEST1", "Odometer": 1000, "OdometerType": "MI"})
        self.assertEqual(listing.odometer_km, 1609)

    def test_record_senza_vin_scartato(self):
        self.assertIsNone(parse_listing({"Year": 2021}))

    def test_etichetta_non_presa_dagli_optional(self):
        """Senza TrimName l'etichetta deve restare generica, non il nome della vernice."""
        listing = parse_listing({
            "VIN": "SENZATRIM", "Year": 2021,
            "OptionCodeData": [{"code": "$PPSW", "name": "Bianco Perla Multistrato"}],
        })
        self.assertNotIn("Bianco", listing.trim_name)
        self.assertTrue(listing.trim_guessed)

    def test_cerchi_riconosciuti_dal_codice_se_manca_il_nome(self):
        maggiorati = parse_listing({"VIN": "W1", "OptionCodeList": "$W41B",
                                    "OptionCodeData": [{"code": "$PPSW", "name": "Bianco"}]})
        base = parse_listing({"VIN": "W2", "OptionCodeList": "$W38B",
                              "OptionCodeData": [{"code": "$PPSW", "name": "Bianco"}]})
        self.assertTrue(maggiorati.has_upgraded_wheels)
        self.assertFalse(base.has_upgraded_wheels)

    def test_cerchi_dal_nome_hanno_la_precedenza(self):
        base = parse_listing({"VIN": "W3",
                              "OptionCodeData": [{"code": "$W38B", "name": 'Cerchi Aero da 18"'}]})
        self.assertFalse(base.has_upgraded_wheels)

    def test_url_annuncio(self):
        listing = parse_listing({"VIN": "ABC", "Model": "m3"}, zip_code="90126")
        self.assertIn("ABC", listing.url)
        self.assertIn("postal=90126", listing.url)


class TestValutazione(unittest.TestCase):
    def setUp(self):
        self.cfg = Config()
        self.listings = parse_all(load_fixture("inventario_esempio.json")["results"])

    def test_curva_di_deprezzamento_decrescente_e_limitata(self):
        previous = 1.01
        for age in [0, 0.5, 1, 2, 3, 5, 8, 12, 30]:
            value = retention_factor(self.cfg.valuation, age)
            self.assertLessEqual(value, previous)
            self.assertGreaterEqual(value, self.cfg.valuation.min_retention)
            previous = value

    def test_garanzia_residua_fra_zero_e_uno(self):
        for listing in self.listings:
            value = warranty_remaining(self.cfg.valuation, listing, listing.age_years or 0)
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)

    def test_annuncio_senza_prezzo_non_valutabile(self):
        senza_prezzo = next(l for l in self.listings if l.effective_price in (None, 0))
        result = evaluate(self.cfg, senza_prezzo)
        self.assertFalse(result.valid)
        self.assertIn("prezzo non disponibile", result.notes)

    def test_convenienza_coerente_col_valore_stimato(self):
        valuations, _ = evaluate_all(self.cfg, self.listings)
        for valuation in valuations:
            if not valuation.valid:
                continue
            self.assertAlmostEqual(valuation.advantage_eur,
                                   valuation.estimated_value - valuation.price, places=6)
            self.assertTrue(0 <= valuation.score <= 100)

    def test_calibrazione_centra_la_mediana_su_zero(self):
        valuations, factor = evaluate_all(self.cfg, self.listings)
        self.assertGreater(factor, 0)
        valid = sorted(v.advantage_pct for v in valuations if v.valid)
        mediana = valid[len(valid) // 2] if len(valid) % 2 else \
            (valid[len(valid) // 2 - 1] + valid[len(valid) // 2]) / 2
        self.assertAlmostEqual(mediana, 0.0, delta=0.5)

    def test_calibrazione_disattivabile(self):
        self.cfg.valuation.auto_calibrate = False
        _, factor = evaluate_all(self.cfg, self.listings)
        self.assertEqual(factor, 1.0)

    def test_calibrazione_salta_con_pochi_dati(self):
        valuations, factor = evaluate_all(self.cfg, self.listings[:2])
        self.assertEqual(factor, 1.0)

    def test_stessa_auto_meno_cara_vince(self):
        """A parita di tutto, un prezzo piu basso deve dare punteggio piu alto."""
        base = next(l for l in self.listings if l.vin == "LRW3E7EK3MC100003")
        import copy
        piu_cara = copy.deepcopy(base)
        piu_cara.vin = "PIUCARA"
        piu_cara.price = base.price * 1.2
        piu_cara.total_price = base.total_price * 1.2
        cfg = Config()
        cfg.valuation.auto_calibrate = False
        valuations, _ = evaluate_all(cfg, [base, piu_cara])
        migliore = rank(valuations)[0]
        self.assertEqual(migliore.listing.vin, base.vin)

    def test_danni_abbassano_il_valore(self):
        cfg = Config()
        cfg.valuation.auto_calibrate = False
        integra = next(l for l in self.listings if l.vin == "LRW3E7EA7PC100007")
        import copy
        danneggiata = copy.deepcopy(integra)
        danneggiata.has_damage = True
        senza = evaluate(cfg, integra)
        con = evaluate(cfg, danneggiata)
        self.assertLess(con.estimated_value, senza.estimated_value)

    def test_ordinamenti(self):
        valuations, _ = evaluate_all(self.cfg, self.listings)
        per_prezzo = rank(valuations, "price")
        self.assertEqual(per_prezzo, sorted(per_prezzo, key=lambda v: v.price))
        per_affare = rank(valuations, "deal")
        self.assertGreaterEqual(per_affare[0].advantage_pct, per_affare[-1].advantage_pct)


class TestFiltri(unittest.TestCase):
    def setUp(self):
        self.cfg = Config()
        self.listings = parse_all(load_fixture("inventario_esempio.json")["results"])

    def test_prezzo_massimo(self):
        self.cfg.filters.max_price = 25000
        kept = apply_filters(self.cfg, self.listings)
        self.assertTrue(all(l.effective_price <= 25000 for l in kept))
        self.assertTrue(kept)

    def test_km_e_anno(self):
        self.cfg.filters.max_odometer = 50000
        self.cfg.filters.min_year = 2022
        kept = apply_filters(self.cfg, self.listings)
        self.assertTrue(all(l.odometer_km <= 50000 and l.year >= 2022 for l in kept))

    def test_allestimento(self):
        self.cfg.filters.trims = [TRIM_PERF]
        kept = apply_filters(self.cfg, self.listings)
        self.assertTrue(kept)
        self.assertTrue(all(l.trim == TRIM_PERF for l in kept))

    def test_esclusione_danni(self):
        self.cfg.filters.exclude_damaged = True
        kept = apply_filters(self.cfg, self.listings)
        self.assertFalse(any(l.has_damage for l in kept))

    def test_annuncio_senza_prezzo_escluso_dal_filtro_prezzo(self):
        self.cfg.filters.max_price = 100000
        kept = apply_filters(self.cfg, self.listings)
        self.assertFalse(any(l.effective_price in (None, 0) for l in kept))


class TestStorico(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = HistoryStore(self.tmp.name)
        self.cfg = Config()

    def tearDown(self):
        self.tmp.cleanup()

    def _valuta(self, fixture):
        listings = parse_all(load_fixture(fixture)["results"])
        valuations, _ = evaluate_all(self.cfg, listings)
        return rank(valuations)

    def test_primo_giro_non_segnala_tutto_come_nuovo(self):
        changes = self.store.diff(self.store.load(), self._valuta("inventario_esempio.json"))
        self.assertTrue(changes.first_run)
        self.assertEqual(changes.new_listings, [])

    def test_variazioni_fra_due_giri(self):
        prima = self._valuta("inventario_esempio.json")
        state = self.store.update(self.store.load(), prima)
        self.store.save(state)

        dopo = self._valuta("inventario_esempio_dopo.json")
        changes = self.store.diff(self.store.load(), dopo)

        self.assertFalse(changes.first_run)
        self.assertEqual([v.listing.vin for v in changes.new_listings], ["LRW3E7EK2SC100012"])
        self.assertEqual([c.valuation.listing.vin for c in changes.price_drops],
                         ["LRW3E7EK3MC100003"])
        self.assertEqual([c.valuation.listing.vin for c in changes.price_rises],
                         ["LRW3E7EA5NC100005"])
        self.assertEqual([r["vin"] for r in changes.removed], ["LRW3E7EA0LC100010"])
        self.assertTrue(changes.has_news)

    def test_nessuna_variazione_se_niente_cambia(self):
        prima = self._valuta("inventario_esempio.json")
        self.store.save(self.store.update(self.store.load(), prima))
        changes = self.store.diff(self.store.load(), self._valuta("inventario_esempio.json"))
        self.assertFalse(changes.has_news)

    def test_storico_prezzi_accumulato(self):
        prima = self._valuta("inventario_esempio.json")
        self.store.save(self.store.update(self.store.load(), prima))
        dopo = self._valuta("inventario_esempio_dopo.json")
        self.store.save(self.store.update(self.store.load(), dopo))
        storico = self.store.price_history(self.store.load(), "LRW3E7EK3MC100003")
        self.assertEqual([p for _, p in storico], [28900.0, 26900.0])

    def test_stato_corrotto_non_blocca(self):
        Path(self.tmp.name, "state.json").write_text("{non json", encoding="utf-8")
        state = self.store.load()
        self.assertEqual(state["vehicles"], {})
        self.assertTrue(state.get("corrupted"))

    def test_registro_esecuzioni(self):
        valutazioni = self._valuta("inventario_esempio.json")
        changes = self.store.diff(self.store.load(), valutazioni)
        self.store.append_run(valutazioni, changes, {"backend": "file"})
        righe = Path(self.tmp.name, "runs.jsonl").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(righe), 1)
        self.assertEqual(json.loads(righe[0])["listings"], len(valutazioni))


class TestFetchHelpers(unittest.TestCase):
    def test_url_pagina_corrisponde_alla_ricerca(self):
        cfg = Config()
        self.assertEqual(
            page_url(cfg.search),
            "https://www.tesla.com/it_IT/inventory/used/m3"
            "?arrangeby=plh&zip=90126&PaymentType=cash")

    def test_query_contiene_i_parametri_chiave(self):
        query = build_query(Config().search, offset=50)["query"]
        self.assertEqual(query["model"], "m3")
        self.assertEqual(query["condition"], "used")
        self.assertEqual(query["zip"], "90126")

    def test_risultati_da_entrambe_le_forme(self):
        self.assertEqual(len(extract_results({"results": [{"VIN": "A"}, {"VIN": "B"}]})), 2)
        self.assertEqual(
            len(extract_results({"results": {"exact": [{"VIN": "A"}],
                                             "approximate": [{"VIN": "B"}]}})), 2)
        self.assertEqual(extract_results({}), [])

    def test_riconoscimento_sfida_antibot(self):
        self.assertTrue(_looks_like_challenge('{"cpr_chlge":"true","t":"1"}'))
        self.assertTrue(_looks_like_challenge("<H1>Access Denied</H1>"))
        self.assertFalse(_looks_like_challenge('{"results": [], "total_matches_found": "0"}'))


class TestReport(unittest.TestCase):
    def setUp(self):
        self.cfg = Config()
        self.cfg.output.color = False
        listings = parse_all(load_fixture("inventario_esempio.json")["results"])
        valuations, self.factor = evaluate_all(self.cfg, listings)
        self.ranked = rank(valuations)
        self.skipped = [v for v in valuations if not v.valid]
        self.changes = HistoryStore(tempfile.gettempdir()).diff(
            {"vehicles": {}, "last_run": None}, self.ranked)
        self.meta = {"backend": "file", "calibration": self.factor}

    def test_console_contiene_la_migliore(self):
        text = report.format_console(self.cfg, self.ranked, self.skipped,
                                     self.changes, self.meta)
        self.assertIn("MIGLIOR RAPPORTO QUALITA/PREZZO", text)
        self.assertIn(self.ranked[0].listing.label, text)

    def test_markdown_ben_formato(self):
        text = report.format_markdown(self.cfg, self.ranked, self.skipped,
                                      self.changes, self.meta)
        self.assertIn("# Tesla usate", text)
        self.assertIn("| # | Punteggio |", text)
        self.assertIn(self.ranked[0].listing.url, text)

    def test_html_autonomo_e_valido(self):
        page = html_report.format_html(self.cfg, self.ranked, self.skipped,
                                       self.changes, self.meta)
        self.assertTrue(page.startswith("<!DOCTYPE html>"))
        self.assertIn('<html lang="it">', page)
        self.assertEqual(page.count("<body>"), 1)
        self.assertNotIn("http://", page.split("<body>")[0])  # nessuna risorsa esterna
        self.assertIn("prefers-color-scheme: dark", page)
        self.assertIn(self.ranked[0].listing.vin, page)

    def test_html_scherma_i_caratteri_speciali(self):
        import copy
        velenoso = copy.deepcopy(self.ranked[0])
        velenoso.listing.trim_name = '<script>alert("x")</script>'
        page = html_report.format_html(self.cfg, [velenoso], [], self.changes, self.meta)
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)

    def test_notifica_breve(self):
        text = report.format_notification(self.cfg, self.ranked, self.changes)
        self.assertIn(self.ranked[0].listing.url, text)
        self.assertLess(len(text), 4000, "deve stare in un messaggio Telegram")

    def test_plurali(self):
        self.assertEqual(report.plural(1, "annuncio", "annunci"), "1 annuncio")
        self.assertEqual(report.plural(3, "annuncio", "annunci"), "3 annunci")

    def test_report_vuoto_non_esplode(self):
        for renderer in (report.format_console, report.format_markdown,
                         html_report.format_html):
            testo = renderer(self.cfg, [], [], self.changes, self.meta)
            self.assertIn("Nessuna auto valutabile", testo)


class TestEsecuzioneCompleta(unittest.TestCase):
    def test_run_once_scrive_report_e_storico(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config()
            cfg.fetch.backend = "file"
            cfg.fetch.source_file = str(FIXTURES / "inventario_esempio.json")
            cfg.output.data_dir = tmp
            cfg.output.color = False

            primo = run_once(cfg)
            self.assertTrue(primo.ranked)
            self.assertTrue(primo.changes.first_run)
            self.assertTrue((Path(tmp) / "state.json").exists())
            self.assertTrue((Path(tmp) / "report" / "ultimo.html").exists())
            self.assertTrue((Path(tmp) / "report" / "ultimo.md").exists())

            cfg.fetch.source_file = str(FIXTURES / "inventario_esempio_dopo.json")
            secondo = run_once(cfg)
            self.assertFalse(secondo.changes.first_run)
            self.assertEqual(len(secondo.changes.new_listings), 1)
            self.assertEqual(len(secondo.changes.price_drops), 1)
            self.assertEqual(len(secondo.changes.removed), 1)

    def test_dry_run_non_scrive(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config()
            cfg.fetch.backend = "file"
            cfg.fetch.source_file = str(FIXTURES / "inventario_esempio.json")
            cfg.output.data_dir = tmp
            run_once(cfg, dry_run=True)
            self.assertEqual(list(Path(tmp).iterdir()), [])


class TestConfigurazione(unittest.TestCase):
    def test_caricamento_parziale(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"search": {"zip_code": "20121"},
                       "filters": {"max_price": 30000},
                       "interval_hours": 6}, handle)
            path = handle.name
        cfg = Config.load(path)
        self.assertEqual(cfg.search.zip_code, "20121")
        self.assertEqual(cfg.filters.max_price, 30000)
        self.assertEqual(cfg.interval_hours, 6)
        self.assertEqual(cfg.search.model, "m3")   # i default restano
        Path(path).unlink()

    def test_chiave_sconosciuta_segnalata(self):
        with self.assertRaises(ValueError):
            Config.from_dict({"search": {"non_esiste": 1}})

    def test_serializzazione_completa(self):
        self.assertIn("valuation", Config().to_dict())


if __name__ == "__main__":
    unittest.main(verbosity=2)
