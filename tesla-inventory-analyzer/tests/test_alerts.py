"""Test della sorveglianza: colori, soglie, archivio, email."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tesla_inventory import alert_report, mailer
from tesla_inventory.alerts import (OpportunityArchive, describe_thresholds,
                                    evaluate_thresholds)
from tesla_inventory.app import matches_filters, run_once
from tesla_inventory.config import Config, EmailConfig
from tesla_inventory.parse import parse_all, parse_listing
from tesla_inventory.valuation import evaluate_all

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
SENZA = FIXTURES / "sorveglianza_nessuna_occasione.json"
CON = FIXTURES / "sorveglianza_occasione.json"

VIN_BLU = "LRW3E7EA7PC200015"      # la 2023 RWD blu
VIN_NERA = "LRW3E7EA5PC200013"
VIN_ROSSA = "LRW3E7EA6PC200014"
VIN_DANNEGGIATA = "LRW3E7EA8PC200016"


def ricerca_di_mirko(data_dir, source):
    """La configurazione richiesta: Model 3 2023 RWD nera, rossa o blu."""
    cfg = Config()
    cfg.fetch.backend = "file"
    cfg.fetch.source_file = str(source)
    cfg.output.data_dir = str(data_dir)
    cfg.output.color = False
    cfg.filters.min_year = 2023
    cfg.filters.max_year = 2023
    cfg.filters.trims = ["RWD"]
    cfg.filters.colors = ["black", "red", "blue"]
    cfg.filters.exclude_damaged = True
    cfg.alert.enabled = True
    return cfg


class TestColori(unittest.TestCase):
    def test_colore_dal_codice(self):
        casi = {"$PBSB": "black", "$PPSW": "white", "$PPSB": "blue",
                "$PPMR": "red", "$PMNG": "grey"}
        for codice, atteso in casi.items():
            listing = parse_listing({"VIN": "X", "OptionCodeList": codice})
            self.assertEqual(listing.color, atteso, msg=codice)

    def test_interni_neri_non_rendono_nera_una_bianca(self):
        """Il caso che romperebbe il filtro colore piu facilmente."""
        listing = parse_listing({"VIN": "X", "OptionCodeData": [
            {"code": "$PPSW", "group": "PAINT", "name": "Bianco Perla Multistrato"},
            {"code": "$IBB1", "group": "INTERIOR", "name": "Interni Neri"}]})
        self.assertEqual(listing.color, "white")

    def test_colore_dal_nome_se_il_codice_e_ignoto(self):
        listing = parse_listing({"VIN": "X", "OptionCodeData": [
            {"code": "$PZZZ", "group": "PAINT", "name": "Blu Notte"},
            {"code": "$IBB1", "group": "INTERIOR", "name": "Interni Neri"}]})
        self.assertEqual(listing.color, "blue")
        self.assertEqual(listing.color_name, "Blu Notte")

    def test_grigio_midnight_silver_non_e_argento(self):
        listing = parse_listing({"VIN": "X", "OptionCodeData": [
            {"code": "$PZZZ", "group": "PAINT", "name": "Grigio Midnight Silver"}]})
        self.assertEqual(listing.color, "grey")

    def test_forma_reale_della_risposta_tesla(self):
        """La forma che l'API restituisce davvero sui mercati europei.

        Nelle catture reali OptionCodeData NON espone il gruppo PAINT (ci sono
        solo AUTOPILOT e SPECS_*), mentre il colore arriva dal campo PAINT di
        primo livello e il codice vernice da OptionCodePricing. Un parser che
        cercasse il gruppo PAINT dentro OptionCodeData non troverebbe nulla.
        """
        record = {
            "VIN": "LRW3E7EA1PC999001",
            "Year": 2023,
            "TrimName": "Trazione Posteriore",
            "Odometer": 30000,
            "OdometerType": "KM",
            "Price": 29000,
            "PAINT": ["BLACK"],
            "OptionCodeList": "$APFS,$DV2W,$IBB1,$PBSB,$W38B",
            "OptionCodeData": [
                {"code": "$APFS", "group": "AUTOPILOT", "name": "Autopilot di base"},
                {"code": "SPECS_RANGE", "group": "SPECS_RANGE", "name": "Autonomia"},
            ],
            "OptionCodePricing": [
                {"code": "$PBSB", "group": "PAINT", "price": 1300},
                {"code": "$IBB1", "group": "INTERIOR", "price": 0},
            ],
        }
        listing = parse_listing(record)
        self.assertEqual(listing.color, "black")
        self.assertEqual(listing.paint_price, 1300.0)

    def test_campo_paint_ha_la_precedenza(self):
        """Il bucket normalizzato da Tesla vince sui codici, che possono mancare."""
        listing = parse_listing({"VIN": "X", "PAINT": ["RED"], "OptionCodeList": "$PIGNOTO"})
        self.assertEqual(listing.color, "red")

    def test_tutti_i_bucket_paint_riconosciuti(self):
        for bucket, atteso in [("BLACK", "black"), ("WHITE", "white"), ("RED", "red"),
                               ("BLUE", "blue"), ("GREY", "grey"), ("GRAY", "grey"),
                               ("SILVER", "silver")]:
            listing = parse_listing({"VIN": "X", "PAINT": [bucket]})
            self.assertEqual(listing.color, atteso, msg=bucket)

    def test_bucket_paint_come_stringa(self):
        listing = parse_listing({"VIN": "X", "PAINT": "BLUE"})
        self.assertEqual(listing.color, "blue")

    def test_codice_vernice_da_optioncodepricing(self):
        """Senza campo PAINT, il codice arriva comunque da OptionCodePricing."""
        listing = parse_listing({"VIN": "X", "OptionCodePricing": [
            {"code": "$PPSB", "group": "PAINT", "price": 1300}]})
        self.assertEqual(listing.color, "blue")
        self.assertEqual(listing.paint_price, 1300.0)

    def test_nomi_commerciali_reali(self):
        """Nomi visti sugli annunci Tesla italiani, pre e post restyling."""
        casi = [
            ("Nero Pastello", "black"), ("Nero", "black"),
            ("Rosso Multistrato", "red"), ("Rosso Micalizzato", "red"),
            ("Ultra Rosso", "red"),
            ("Blu Metallizzato", "blue"), ("Blu Oceano Metallizzato", "blue"),
            ("Bianco Perla Multistrato", "white"), ("Bianco Perla Micalizzato", "white"),
            ("Grigio Stealth", "grey"), ("Grigio Midnight Silver", "grey"),
            ("Quicksilver", "silver"), ("Argento vivo", "silver"),
        ]
        for nome, atteso in casi:
            listing = parse_listing({"VIN": "X", "OptionCodeData": [
                {"code": "$PIGNOTO", "group": "PAINT", "name": nome}]})
            self.assertEqual(listing.color, atteso, msg=nome)

    def test_grigio_batte_argento_quando_il_nome_dice_grigio(self):
        """"Grigio Midnight Silver" contiene entrambe le parole: deve vincere grigio."""
        listing = parse_listing({"VIN": "X", "OptionCodeData": [
            {"code": "$PIGNOTO", "group": "PAINT", "name": "Grigio Midnight Silver"}]})
        self.assertEqual(listing.color, "grey")

    def test_colore_assente_resta_vuoto(self):
        listing = parse_listing({"VIN": "X", "OptionCodeData": [
            {"code": "$IBB1", "group": "INTERIOR", "name": "Interni Neri"}]})
        self.assertEqual(listing.color, "")

    def test_filtro_colore(self):
        cfg = Config()
        cfg.filters.colors = ["black", "red", "blue"]
        listings = parse_all(json.loads(SENZA.read_text(encoding="utf-8"))["results"])
        tenute = [l for l in listings if matches_filters(cfg.filters, l)]
        self.assertTrue(tenute)
        self.assertTrue(all(l.color in ("black", "red", "blue") for l in tenute))

    def test_auto_senza_colore_esclusa_dal_filtro(self):
        cfg = Config()
        cfg.filters.colors = ["black"]
        senza_colore = parse_listing({"VIN": "X", "Year": 2023})
        self.assertFalse(matches_filters(cfg.filters, senza_colore))


class TestRiferimentoIndipendenteDaiFiltri(unittest.TestCase):
    """La decisione strutturale: si valuta tutto, si filtra dopo."""

    def test_filtrare_non_cambia_la_valutazione(self):
        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            largo = Config()
            largo.fetch.backend = "file"
            largo.fetch.source_file = str(CON)
            largo.output.data_dir = tmp1
            stretto = ricerca_di_mirko(tmp2, CON)

            senza_filtri = run_once(largo, dry_run=True)
            con_filtri = run_once(stretto, dry_run=True)

            a = next(v for v in senza_filtri.ranked if v.listing.vin == VIN_BLU)
            b = next(v for v in con_filtri.ranked if v.listing.vin == VIN_BLU)
            self.assertAlmostEqual(a.advantage_pct, b.advantage_pct, places=6)
            self.assertAlmostEqual(a.estimated_value, b.estimated_value, places=6)
            self.assertEqual(a.score, b.score)

    def test_riferimento_usa_tutto_linventario(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = ricerca_di_mirko(tmp, CON)
            result = run_once(cfg, dry_run=True)
            self.assertGreater(result.baseline_count, len(result.ranked))
            # nera pre-restyling, nera Highland, rossa, blu; la danneggiata e esclusa
            self.assertEqual(len(result.ranked), 4)


class TestSoglie(unittest.TestCase):
    def setUp(self):
        self.cfg = Config()
        listings = parse_all(json.loads(CON.read_text(encoding="utf-8"))["results"])
        self.valuations, _ = evaluate_all(self.cfg, listings)
        self.by_vin = {v.listing.vin: v for v in self.valuations}

    def test_occasione_supera_le_soglie(self):
        ok, motivi = evaluate_thresholds(self.cfg.alert, self.by_vin[VIN_BLU])
        self.assertTrue(ok)
        self.assertTrue(any("punteggio" in m for m in motivi))

    def test_auto_normale_non_supera(self):
        ok, motivi = evaluate_thresholds(self.cfg.alert, self.by_vin[VIN_NERA])
        self.assertFalse(ok)
        self.assertTrue(motivi, "deve spiegare cosa e mancato")

    def test_budget_esclude_le_troppo_care(self):
        self.cfg.alert.max_price = 20000
        ok, motivi = evaluate_thresholds(self.cfg.alert, self.by_vin[VIN_BLU])
        self.assertFalse(ok)
        self.assertTrue(any("budget" in m for m in motivi))

    def test_valutazione_non_valida_non_e_occasione(self):
        non_valida = next(v for v in self.valuations if not v.valid) \
            if any(not v.valid for v in self.valuations) else None
        if non_valida is not None:
            self.assertFalse(evaluate_thresholds(self.cfg.alert, non_valida)[0])

    def test_descrizione_soglie_leggibile(self):
        self.cfg.alert.max_price = 32000
        testo = describe_thresholds(self.cfg.alert)
        self.assertIn("punteggio", testo)
        self.assertIn("32.000", testo)


class TestArchivio(unittest.TestCase):
    def test_ciclo_completo_di_segnalazione(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 1. Nessuna occasione: niente da archiviare.
            cfg = ricerca_di_mirko(tmp, SENZA)
            primo = run_once(cfg)
            self.assertEqual(primo.qualifying, [])
            self.assertEqual(primo.opportunities, [])

            # 2. Il prezzo crolla: l'occasione compare e va segnalata.
            cfg = ricerca_di_mirko(tmp, CON)
            secondo = run_once(cfg)
            self.assertEqual(len(secondo.qualifying), 1)
            self.assertEqual(len(secondo.opportunities), 1)
            self.assertEqual(secondo.opportunities[0].valuation.listing.vin, VIN_BLU)
            self.assertTrue(secondo.opportunities[0].first_time)

            # 3. Stesso giro: l'auto resta un'occasione ma non si ri-segnala.
            terzo = run_once(cfg)
            self.assertEqual(len(terzo.qualifying), 1)
            self.assertEqual(terzo.opportunities, [], "non deve avvisare due volte")

            archivio = json.loads(Path(tmp, "occasioni.json").read_text(encoding="utf-8"))
            self.assertIn(VIN_BLU, archivio["occasioni"])
            self.assertIn("notificata_il", archivio["occasioni"][VIN_BLU])

    def test_ulteriore_ribasso_fa_riavvisare(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = ricerca_di_mirko(tmp, CON)
            run_once(cfg)

            archive = OpportunityArchive(tmp)
            state = archive.load()
            state["occasioni"][VIN_BLU]["prezzo_notificato"] = 30000.0   # come se costasse di piu
            archive.save(state)

            di_nuovo = run_once(cfg)
            self.assertEqual(len(di_nuovo.opportunities), 1)
            self.assertFalse(di_nuovo.opportunities[0].first_time)
            self.assertLess(di_nuovo.opportunities[0].drop_since_notified, 0)

    def test_ribasso_troppo_piccolo_non_riavvisa(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = ricerca_di_mirko(tmp, CON)
            run_once(cfg)
            archive = OpportunityArchive(tmp)
            state = archive.load()
            # Solo 100 euro sopra: sotto la soglia di ripetizione (500).
            state["occasioni"][VIN_BLU]["prezzo_notificato"] = 25000.0
            archive.save(state)
            self.assertEqual(run_once(cfg).opportunities, [])

    def test_archivio_corrotto_non_blocca(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "occasioni.json").write_text("{rotto", encoding="utf-8")
            cfg = ricerca_di_mirko(tmp, CON)
            result = run_once(cfg)
            self.assertEqual(len(result.opportunities), 1)

    def test_dry_run_non_archivia(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = ricerca_di_mirko(tmp, CON)
            run_once(cfg, dry_run=True)
            self.assertFalse(Path(tmp, "occasioni.json").exists())


class TestEmail(unittest.TestCase):
    def _config(self):
        return EmailConfig(enabled=True, smtp_host="smtp.example.com", smtp_port=587,
                           username="io@example.com", password="segreta",
                           sender="io@example.com",
                           recipients=["mirkoterranova@outlook.it"])

    def test_configurazione_incompleta_elencata(self):
        problemi = mailer.check_config(EmailConfig())
        self.assertTrue(any("smtp_host" in p for p in problemi))
        self.assertTrue(any("destinatario" in p for p in problemi))

    def test_password_da_variabile_ambiente(self):
        cfg = EmailConfig(password_env="TEST_TESLA_PWD")
        with mock.patch.dict("os.environ", {"TEST_TESLA_PWD": "dalla-variabile"}):
            self.assertEqual(mailer.resolve_password(cfg), "dalla-variabile")

    def test_messaggio_ben_formato(self):
        cfg = self._config()
        messaggio = mailer.build_message(cfg, "Oggetto", "testo", "<p>html</p>")
        self.assertEqual(messaggio["To"], "mirkoterranova@outlook.it")
        self.assertIn("Oggetto", messaggio["Subject"])
        self.assertIn(cfg.subject_prefix, messaggio["Subject"])
        self.assertTrue(messaggio.is_multipart(), "serve anche la versione HTML")

    def test_invio_usa_starttls_e_login(self):
        cfg = self._config()
        finto = mock.MagicMock()
        finto.__enter__ = mock.Mock(return_value=finto)
        finto.__exit__ = mock.Mock(return_value=False)
        with mock.patch("smtplib.SMTP", return_value=finto) as costruttore:
            mailer.send(cfg, "Oggetto", "testo")
        costruttore.assert_called_once()
        finto.starttls.assert_called_once()
        finto.login.assert_called_once_with("io@example.com", "segreta")
        finto.send_message.assert_called_once()

    def test_credenziali_rifiutate_spiegano_le_password_per_app(self):
        import smtplib
        cfg = self._config()
        finto = mock.MagicMock()
        finto.__enter__ = mock.Mock(return_value=finto)
        finto.__exit__ = mock.Mock(return_value=False)
        finto.login.side_effect = smtplib.SMTPAuthenticationError(535, b"rifiutato")
        with mock.patch("smtplib.SMTP", return_value=finto):
            with self.assertRaises(mailer.EmailError) as ctx:
                mailer.send(cfg, "Oggetto", "testo")
        self.assertIn("password per le app", str(ctx.exception))

    def test_email_non_inviata_non_marca_come_segnalata(self):
        """Se l'email fallisce, l'occasione deve tornare al giro successivo."""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = ricerca_di_mirko(tmp, CON)
            cfg.email = self._config()
            with mock.patch("tesla_inventory.mailer.send",
                            side_effect=mailer.EmailError("server irraggiungibile")):
                primo = run_once(cfg)
            self.assertEqual(len(primo.opportunities), 1)
            self.assertFalse(primo.email_sent)
            self.assertTrue(any("Email non inviata" in p for p in primo.notify_problems))

            with mock.patch("tesla_inventory.mailer.send") as invio:
                secondo = run_once(cfg)
            self.assertEqual(len(secondo.opportunities), 1, "deve riprovare")
            invio.assert_called_once()

    def test_invio_riuscito_marca_come_segnalata(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = ricerca_di_mirko(tmp, CON)
            cfg.email = self._config()
            with mock.patch("tesla_inventory.mailer.send"):
                primo = run_once(cfg)
                self.assertTrue(primo.email_sent)
                secondo = run_once(cfg)
            self.assertEqual(secondo.opportunities, [])


class TestContenutoAvviso(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = ricerca_di_mirko(self.tmp.name, CON)
        self.result = run_once(self.cfg, dry_run=True)
        self.opportunities = OpportunityArchive(self.tmp.name).to_notify(
            self.cfg, {"occasioni": {}}, self.result.qualifying)

    def tearDown(self):
        self.tmp.cleanup()

    def test_oggetto_dice_prezzo_e_convenienza(self):
        testo = alert_report.subject(self.opportunities)
        self.assertIn("24.900", testo)
        self.assertIn("Occasione", testo)

    def test_testo_contiene_i_dati_essenziali(self):
        corpo = alert_report.text_body(self.cfg, self.opportunities, self.result.meta)
        for atteso in ("24.900", "28.000 km", "Blu", self.opportunities[0].valuation.listing.url):
            self.assertIn(atteso, corpo)

    def test_html_email_senza_stili_esterni(self):
        corpo = alert_report.html_body(self.cfg, self.opportunities, self.result.meta)
        self.assertIn("<table", corpo)
        self.assertNotIn("<style", corpo, "i client di posta ignorano i fogli di stile")
        self.assertIn("24.900", corpo)

    def test_html_email_scherma_i_caratteri(self):
        self.opportunities[0].valuation.listing.color_name = '<img src=x onerror=1>'
        corpo = alert_report.html_body(self.cfg, self.opportunities, self.result.meta)
        self.assertNotIn("<img src=x", corpo)
        self.assertIn("&lt;img", corpo)

    def test_verdetto_console_senza_occasioni(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = ricerca_di_mirko(tmp, SENZA)
            result = run_once(cfg, dry_run=True)
            testo = alert_report.format_console_verdict(cfg, result, color=False)
            self.assertIn("NESSUNA OCCASIONE", testo)
            self.assertIn("Le manca", testo)

    def test_verdetto_console_con_occasione(self):
        testo = alert_report.format_console_verdict(self.cfg, self.result, color=False)
        self.assertIn("OCCASIONE TROVATA", testo)
        self.assertIn("24.900", testo)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestGenerazione(unittest.TestCase):
    """Una Model 3 del 2023 puo essere pre-restyling o Highland: vale diverso."""

    def test_riconoscimento_dalla_autonomia(self):
        from tesla_inventory.parse import GEN_HIGHLAND, GEN_PRE, detect_generation
        self.assertEqual(detect_generation("RWD", 2023, 491), GEN_PRE)
        self.assertEqual(detect_generation("RWD", 2023, 513), GEN_HIGHLAND)
        self.assertEqual(detect_generation("RWD", 2022, 491), GEN_PRE)
        self.assertEqual(detect_generation("RWD", 2025, 554), GEN_HIGHLAND)
        self.assertEqual(detect_generation("RWD", 2023, None), "",
                         "senza autonomia il 2023 resta ambiguo")

    def test_highland_vale_piu_della_pre_restyling(self):
        cfg = Config()
        cfg.valuation.auto_calibrate = False
        base = {"VIN": "G1", "Year": 2023, "TrimName": "Trazione Posteriore",
                "Odometer": 30000, "OdometerType": "KM", "Price": 29000,
                "OptionCodeData": [{"code": "$PBSB", "group": "PAINT", "name": "Nero Pastello"}]}
        pre = dict(base, VIN="PRE", OptionCodeSpecs={"C_SPECS": {"options": [
            {"code": "SPECS_RANGE", "name": "Autonomia (WLTP)", "value": "491"}]}})
        high = dict(base, VIN="HIGH", OptionCodeSpecs={"C_SPECS": {"options": [
            {"code": "SPECS_RANGE", "name": "Autonomia (WLTP)", "value": "513"}]}})

        valuations, _ = evaluate_all(cfg, parse_all([pre, high]))
        per_vin = {v.listing.vin: v for v in valuations}
        self.assertGreater(per_vin["HIGH"].estimated_value, per_vin["PRE"].estimated_value)
        self.assertGreater(per_vin["HIGH"].score, per_vin["PRE"].score,
                           "a parita di prezzo la Highland deve risultare l'affare migliore")

    def test_generazione_ignota_segnalata_nelle_note(self):
        cfg = Config()
        senza_autonomia = parse_all([{"VIN": "G2", "Year": 2023, "Price": 29000,
                                      "TrimName": "Trazione Posteriore"}])
        valuations, _ = evaluate_all(cfg, senza_autonomia)
        self.assertTrue(any("generazione" in n for n in valuations[0].notes))

    def test_generazione_compare_nei_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = ricerca_di_mirko(tmp, CON)
            result = run_once(cfg, dry_run=True)
            from tesla_inventory import html_report, report
            testo = report.format_console(cfg, result.ranked, result.skipped,
                                          result.changes, result.meta)
            pagina = html_report.format_html(cfg, result.ranked, result.skipped,
                                             result.changes, result.meta)
            self.assertIn("restyling", testo)
            self.assertIn("Generazione", pagina)


class TestCalibrazionePerAllestimento(unittest.TestCase):
    """L'ancora di un allestimento e incerta: la calibrazione deve assorbirla."""

    def _valuta(self, prezzo_nuovo_rwd):
        cfg = Config()
        cfg.valuation.new_prices = dict(cfg.valuation.new_prices, RWD=prezzo_nuovo_rwd)
        listings = parse_all(json.loads(CON.read_text(encoding="utf-8"))["results"])
        valuations, _ = evaluate_all(cfg, listings)
        return {v.listing.vin: v for v in valuations if v.valid}

    def test_ancora_sbagliata_non_sposta_il_confronto_fra_rwd(self):
        giusta = self._valuta(36990.0)
        gonfiata = self._valuta(44000.0)      # ancora sbagliata del 19% in eccesso
        sgonfiata = self._valuta(30000.0)     # e del 19% in difetto

        for vin in (VIN_BLU, VIN_NERA, VIN_ROSSA):
            self.assertAlmostEqual(giusta[vin].advantage_pct,
                                   gonfiata[vin].advantage_pct, delta=1.5,
                                   msg=f"{vin} con ancora gonfiata")
            self.assertAlmostEqual(giusta[vin].advantage_pct,
                                   sgonfiata[vin].advantage_pct, delta=1.5,
                                   msg=f"{vin} con ancora sgonfiata")

    def test_occasione_resta_occasione_con_ancora_diversa(self):
        cfg = Config()
        for prezzo in (30000.0, 36990.0, 44000.0):
            cfg.valuation.new_prices = dict(cfg.valuation.new_prices, RWD=prezzo)
            listings = parse_all(json.loads(CON.read_text(encoding="utf-8"))["results"])
            valuations, _ = evaluate_all(cfg, listings)
            blu = next(v for v in valuations if v.listing.vin == VIN_BLU)
            self.assertTrue(evaluate_thresholds(cfg.alert, blu)[0],
                            msg=f"con ancora {prezzo} l'occasione non viene piu vista")

    def test_allestimenti_calibrati_separatamente(self):
        cfg = Config()
        listings = parse_all(json.loads(CON.read_text(encoding="utf-8"))["results"])
        valuations, _ = evaluate_all(cfg, listings)
        per_allestimento = {}
        for v in valuations:
            if v.valid:
                per_allestimento.setdefault(v.listing.trim, set()).add(round(v.calibration, 6))
        for trim, fattori in per_allestimento.items():
            self.assertEqual(len(fattori), 1, f"{trim} deve avere un solo fattore")
        self.assertGreaterEqual(len(per_allestimento), 2)
