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

## Sorveglianza: fatti avvisare solo quando conviene davvero

La modalità normale mostra una classifica a ogni giro. Quella di sorveglianza fa
una cosa diversa: tace finché non trova un'auto che vale la pena comprare, e
quando la trova la archivia e ti manda una email.

```bash
python3 -m tesla_inventory --config config.json --loop
```

Esempio pronto per «Model 3 del 2023, trazione posteriore, nera rossa o blu»:

```bash
cp config.sorveglianza.json config.json
# completa la sezione email, poi:
export TESLA_SMTP_PASSWORD='la-password-per-le-app'
python3 -m tesla_inventory --config config.json --test-email   # verifica l'invio
python3 -m tesla_inventory --config config.json --loop         # controllo ogni 3 ore
```

Oppure tutto da riga di comando, senza file:

```bash
python3 -m tesla_inventory --alert \
  --min-year 2023 --max-year 2023 --trim RWD \
  --color black --color red --color blue --no-damaged
```

### Cosa conta come occasione

Un'auto viene segnalata solo se supera **tutte** le soglie che hai impostato:

| Soglia | Predefinito | Significato |
|---|--:|---|
| `min_score` | 60 | punteggio complessivo su 100 |
| `min_advantage_pct` | 8% | quanto costa meno del valore stimato |
| `min_advantage_eur` | 0 | lo stesso, in euro |
| `max_price` | nessuno | tetto di spesa |

Quando nessuna auto le supera, il programma non tace e basta: dice quale ci è
andata più vicino e cosa le è mancato.

```
  NESSUNA OCCASIONE in questo momento
  3 auto corrispondono ai tuoi criteri, nessuna supera le soglie.

  La più vicina: 2023 Model 3 Trazione Posteriore a 28.600 € — punteggio 56,2
  Le manca: punteggio 56,2 (soglia 60); convenienza 2,5% (soglia 8%)
```

Se ricevi troppe segnalazioni alza `min_advantage_pct`; se non ne ricevi mai,
abbassala.

### Perché non ricevi otto email al giorno per la stessa auto

Ogni occasione finisce in `data/occasioni.json` e resta lì anche dopo che l'auto
è stata venduta. Una stessa auto torna a essere una notizia **solo** se il prezzo
scende di almeno `repeat_after_drop_eur` (500 € di default) rispetto all'ultimo
avviso.

```bash
python3 -m tesla_inventory --config config.json --archivio   # cosa ho trovato finora
```

Se l'invio dell'email fallisce, l'occasione **non** viene segnata come
comunicata: torna al giro successivo. Un errore di rete non deve farti perdere
l'auto.

### Impostare l'email

Il tuo indirizzo Outlook va benissimo per **ricevere**. È come mittente che dà
problemi: dal 16 settembre 2024 Microsoft ha disattivato per default
l'autenticazione SMTP con password sugli account Outlook, Hotmail e Live
personali, comprese le password per app, e l'invio viene di norma rifiutato con
un errore 535. Il comportamento non è uniforme — su qualche casella più vecchia
può ancora passare — ma non è una base su cui costruire qualcosa che deve
funzionare per mesi. Il programma ti avvisa prima di provarci, invece di fallire
in silenzio.

La strada più semplice è un account Gmail dedicato:

1. crea un indirizzo Gmail usato solo da questo programma;
2. attivaci la verifica in due passaggi (è obbligatoria per il passo dopo);
3. genera una password per le app da `myaccount.google.com/apppasswords`;
4. mettila in una variabile d'ambiente, non nel file di configurazione:

```bash
export TESLA_SMTP_PASSWORD='xxxx xxxx xxxx xxxx'
```

Il campo `sender` deve essere lo stesso indirizzo Gmail autenticato: se metti lì
il tuo indirizzo Outlook i controlli antispam spostano il messaggio nella posta
indesiderata. Dopo il primo invio riuscito, aggiungi l'indirizzo Gmail ai
mittenti attendibili di Outlook.

In alternativa funziona qualsiasi servizio SMTP: basta cambiare `smtp_host`,
`smtp_port` e le credenziali.

### Filtro per colore

`--color black --color red --color blue`, oppure `colors` nel file di
configurazione. I valori sono `black`, `white`, `blue`, `red`, `grey`, `silver`.

Il colore viene letto in tre passaggi, nell'ordine in cui le risposte reali lo
offrono davvero: prima il campo `PAINT` che Tesla restituisce già normalizzato
(`BLACK`, `RED`, `BLUE`…), poi il codice della vernice preso da
`OptionCodePricing`, infine il nome commerciale della **sola** voce vernice.

Quest'ultimo dettaglio non è pedanteria: cercare "nero" fra tutti gli optional
farebbe passare per nera qualsiasi auto con gli interni neri. E il primo passaggio
nemmeno: sulle risposte europee il blocco `OptionCodeData` spesso non contiene
affatto la vernice, quindi un parser che cercasse il colore solo lì non
troverebbe niente.

Un'auto di cui Tesla non dichiara il colore viene esclusa quando il filtro è
attivo, non inclusa per scrupolo: meglio non segnalarla che segnalartene una del
colore sbagliato.

### Pre-restyling o Highland

Una Model 3 immatricolata nel 2023 può essere del progetto originale oppure del
restyling "Highland", arrivato in Italia verso ottobre 2023. A parità di anno e
chilometri la Highland vale sensibilmente di più, quindi una "2023" che costa
poco spesso non è un affare: è semplicemente la generazione precedente.

Il programma le distingue dall'autonomia WLTP dichiarata (491/510 km la RWD
pre-restyling, 513/554 km la Highland), applica la differenza di valore e scrive
la generazione nel report e nell'email. Quando l'annuncio non riporta
l'autonomia, il 2023 resta ambiguo e viene segnalato come tale invece di essere
indovinato.

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

**La calibrazione lavora per allestimento**, non sull'intera gamma. Il listino di
riferimento di un allestimento è per forza approssimato — la gamma 2026 è stata
rinominata e nessuna versione nuova corrisponde esattamente a una usata — e un
fattore unico trasferirebbe quell'errore su tutti gli altri: se l'ancora della
trazione posteriore fosse troppo bassa, tutte le RWD risulterebbero care rispetto
alle Long Range e non verrebbero mai segnalate, per un errore di taratura e non
per il loro prezzo. Con la calibrazione separata, spostare l'ancora della RWD del
19% in più o in meno lascia il giudizio praticamente invariato; c'è un test che
lo verifica.

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

103 test coprono lettura dei dati, riconoscimento di colore e generazione,
valutazione, calibrazione, filtri, soglie delle occasioni, archivio, invio email
e generazione dei report, usando i dati di esempio in `fixtures/`.

## Struttura

```
tesla_inventory/
├── config.py        parametri e caricamento del file JSON
├── fetch.py         download (http · browser · file), riconoscimento anti-bot
├── parse.py         normalizzazione difensiva dei record Tesla
├── valuation.py     valore stimato, calibrazione e punteggio
├── history.py       confronto fra un giro e l'altro
├── alerts.py        soglie delle occasioni e archivio
├── report.py        console, Markdown, testo per le notifiche
├── html_report.py   report HTML autonomo
├── alert_report.py  oggetto, testo e HTML dell'email di avviso
├── mailer.py        invio SMTP
├── notify.py        Telegram e webhook
├── app.py           orchestrazione di una singola analisi
└── __main__.py      riga di comando e ciclo ogni 3 ore
```

## Avvertenza

Il valore stimato è il risultato di un modello statistico configurabile, **non di
una perizia**. Serve a ordinare le occasioni e a segnalare quelle interessanti:
prima di comprare, apri sempre l'annuncio originale e verifica storia del
veicolo, stato della batteria e condizioni di garanzia.
