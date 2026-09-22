# Analizzatore inventario Tesla usato

Controlla ogni 3 ore l'inventario Tesla delle auto usate e ti dice **qual è
l'auto con il miglior rapporto qualità/prezzo**, cosa è cambiato dall'ultimo
controllo (nuovi annunci, ribassi, auto vendute) e perché una certa auto conviene
più delle altre.

Ricerca predefinita — la stessa di questo indirizzo:

```
https://www.tesla.com/it_IT/inventory/used/m3?arrangeby=plh&zip=90126&PaymentType=cash
```

Model 3 usate, CAP 90126, pagamento in contanti. Tutto è configurabile: modello,
CAP, mercato, filtri di prezzo e chilometraggio.

## Cosa serve

Solo **Python 3.10 o superiore**. Nessuna dipendenza da installare: il programma
usa esclusivamente la libreria standard. Playwright serve soltanto per il backend
`browser`, che è facoltativo.

## Avvio rapido

```bash
cd tesla-inventory-analyzer

# Una analisi adesso
python3 -m tesla_inventory

# Controllo automatico ogni 3 ore (resta in esecuzione)
python3 -m tesla_inventory --loop

# Prova senza rete, sui dati di esempio inclusi
python3 -m tesla_inventory --backend file --source-file fixtures/inventario_esempio.json
```

Ad ogni giro vengono scritti in `data/`:

| File | Contenuto |
|---|---|
| `report/ultimo.html` | Report grafico da aprire nel browser |
| `report/ultimo.md` | Stesso report in Markdown |
| `report/report-<data>.md` | Copia storica di ogni analisi |
| `state.json` | Fotografia dell'inventario, per capire cosa cambia |
| `runs.jsonl` | Registro delle esecuzioni, una riga per giro |

## Programmarlo ogni 3 ore

### Modalità continua (la più semplice)

```bash
./esegui.sh                      # riparte da solo se il processo muore
python3 -m tesla_inventory --loop --every 3
```

### cron (Linux, macOS)

```bash
python3 -m tesla_inventory --cron-line     # stampa la riga pronta
crontab -e                                 # e incollala
```

Produce una riga di questo tipo:

```cron
0 */3 * * * cd /percorso/tesla-inventory-analyzer && /usr/bin/python3 -m tesla_inventory --quiet >> data/cron.log 2>&1
```

### systemd (Linux, parte anche dopo un riavvio)

`~/.config/systemd/user/tesla-inventory.service`

```ini
[Unit]
Description=Analisi inventario Tesla usato

[Service]
Type=oneshot
WorkingDirectory=%h/tesla-inventory-analyzer
ExecStart=/usr/bin/python3 -m tesla_inventory --quiet
```

`~/.config/systemd/user/tesla-inventory.timer`

```ini
[Unit]
Description=Controllo inventario Tesla ogni 3 ore

[Timer]
OnBootSec=5min
OnUnitActiveSec=3h
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl --user enable --now tesla-inventory.timer
```

### launchd (macOS)

Salva in `~/Library/LaunchAgents/it.tesla.inventario.plist` un `StartInterval`
di `10800` secondi con `ProgramArguments` `["/usr/bin/python3", "-m",
"tesla_inventory", "--quiet"]` e `WorkingDirectory` sulla cartella del progetto,
poi `launchctl load` del file.

### Windows

Utilità di pianificazione → attività di base → ripeti ogni 3 ore → azione
`python -m tesla_inventory --quiet`, con "Inizio" impostato sulla cartella del
progetto.

## Come viene scelta la "migliore"

Il punteggio da 0 a 100 combina quattro cose:

| Componente | Peso | Cosa misura |
|---|--:|---|
| Convenienza | 45% | Quanto il prezzo è sotto il valore stimato |
| Percorrenza | 20% | Chilometri assoluti e rispetto alla media attesa |
| Garanzia | 20% | Quanto resta della garanzia batteria e motore (8 anni) |
| Dotazione | 15% | Optional presenti e autonomia WLTP |

### Il valore stimato

```
valore = listino_nuovo_attuale × residuo(età)
       + correzione_chilometri
       + valore_optional
       + garanzia_residua
       − penalità_danni
```

Due scelte meritano una spiegazione.

**Si parte dal listino attuale del nuovo, non da quello dell'anno di
immatricolazione.** Tesla ha tagliato i listini nel 2023: una Model 3 del 2022
pagata 59.990 € non vale "il 67% di 59.990", perché oggi la stessa auto nuova ne
costa 50.490. Ancorare al prezzo corrente evita di sovrastimare sistematicamente
il valore delle auto più vecchie.

**Le stime vengono poi ricalibrate sull'inventario osservato**, in modo che
l'auto mediana risulti a convenienza zero. È la parte che rende il risultato
affidabile: il modello di deprezzamento è per forza approssimato (listini che
cambiano, allestimenti dedotti, optional non sempre dichiarati), ma se tutte le
stime sbagliano più o meno nello stesso modo, il *confronto* fra auto resta
valido. Così "+8%" significa «costa l'8% meno di quello che ti aspetteresti viste
le altre Tesla in vendita adesso» — che è esattamente la domanda a cui serve
rispondere. La calibrazione si disattiva con `auto_calibrate: false`.

La curva di deprezzamento predefinita (82% dopo un anno, 63% dopo tre, 48% dopo
cinque) riflette l'andamento tipico della Model 3 in Europa. Tutti i numeri
stanno in `config.esempio.json`: se li ritieni sbagliati, cambiali.

## Configurazione

```bash
cp config.esempio.json config.json
python3 -m tesla_inventory --config config.json
```

Nel file JSON servono solo le voci che vuoi cambiare; il resto resta ai valori
predefiniti. Le chiavi che iniziano con `_` sono commenti e vengono ignorate.

Le opzioni più usate sono disponibili anche da riga di comando:

```bash
python3 -m tesla_inventory --max-price 30000 --max-km 80000 --min-year 2021
python3 -m tesla_inventory --trim LR --trim PERF        # solo questi allestimenti
python3 -m tesla_inventory --rank deal                  # ordina per pura convenienza
python3 -m tesla_inventory --zip 20121 --model my       # Model Y a Milano
python3 -m tesla_inventory --json                       # output per altri strumenti
python3 -m tesla_inventory --help                       # tutte le opzioni
```

## Notifiche (facoltative)

Compila in `config.json` la sezione `notify` per ricevere un messaggio quando
qualcosa cambia:

```json
"notify": {
  "telegram_bot_token": "123456:ABC-DEF...",
  "telegram_chat_id": "987654321",
  "only_on_change": true
}
```

Il token si ottiene da [@BotFather](https://t.me/botfather); il `chat_id` da
[@userinfobot](https://t.me/userinfobot). In alternativa `webhook_url` invia un
POST JSON a un indirizzo qualsiasi (Slack, Discord, n8n, Home Assistant…).

Con `only_on_change: true` la notifica parte solo quando c'è davvero una novità,
così un controllo ogni 3 ore non diventa una sveglia continua.

## Se Tesla blocca le richieste

Il sito è protetto da Akamai. Da una normale connessione domestica il backend
predefinito (`http`) funziona senza problemi, ma da un server, una VPS, un
runner di CI o alcune VPN le richieste ricevono una verifica anti-bot: il
programma se ne accorge, lo dice in chiaro ed esce con codice `2`.

In quel caso ci sono due strade:

```bash
# 1. Eseguire il programma dalla rete di casa (la soluzione consigliata)

# 2. Usare un browser vero, che esegue il JavaScript della pagina
pip install playwright && playwright install chromium
python3 -m tesla_inventory --backend browser
```

Per lavorare offline su una risposta già salvata:

```bash
python3 -m tesla_inventory --save-raw                     # salva il JSON grezzo
python3 -m tesla_inventory --backend file --source-file data/report/grezzo-....json
```

## Se Tesla cambia i nomi dei campi

L'API dell'inventario non ha uno schema pubblico garantito. Il parser è scritto
per resistere: cerca ogni valore su più chiavi alternative, riconosce gli
optional sia dal codice (`$APF2`) sia dal nome tradotto, e se un dato manca lo
segnala nelle note invece di inventarselo.

Se qualcosa smette di essere letto correttamente:

```bash
python3 -m tesla_inventory --save-raw --dry-run   # poi apri il JSON e confronta
```

e aggiungi la chiave nuova all'elenco in `first_of(...)` dentro
`tesla_inventory/parse.py`.

## Test

```bash
python3 -m unittest discover -s tests -v
```

53 test coprono lettura dei dati, valutazione, calibrazione, filtri, storico e
generazione dei report, usando i dati di esempio in `fixtures/`.

## Struttura

```
tesla_inventory/
├── config.py        parametri e caricamento del file JSON
├── fetch.py         download (http · browser · file), riconoscimento anti-bot
├── parse.py         normalizzazione difensiva dei record Tesla
├── valuation.py     valore stimato, calibrazione e punteggio
├── history.py       confronto fra un giro e l'altro
├── report.py        console, Markdown, testo per le notifiche
├── html_report.py   report HTML autonomo
├── notify.py        Telegram e webhook
├── app.py           orchestrazione di una singola analisi
└── __main__.py      riga di comando e ciclo ogni 3 ore
```

## Avvertenza

Il valore stimato è il risultato di un modello statistico configurabile, **non di
una perizia**. Serve a ordinare le occasioni e a segnalare quelle interessanti:
prima di comprare, apri sempre l'annuncio originale e verifica storia del
veicolo, stato della batteria e condizioni di garanzia.
