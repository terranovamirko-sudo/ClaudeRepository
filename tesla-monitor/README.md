# Monitor Tesla Model 3 usate

Controlla ogni ora l'[inventario usato Tesla Italia](https://www.tesla.com/it_IT/inventory/used/m3?arrangeby=plh&zip=90146&range=0&PAINT=WHITE,BLACK)
e ti manda un messaggio quando compare una **Model 3 Trazione Posteriore del 2023, bianca o nera**, che non aveva ancora visto.

- Al **primo avvio** ricevi un riepilogo con tutte le auto già disponibili.
- Poi ricevi un messaggio **solo per le auto nuove**, con prezzo, km, città e link diretto.
- Se Tesla blocca il controllo per 3 volte di fila ricevi un avviso ⚠️, così sai che il monitor non sta funzionando.

Le notifiche arrivano su **Telegram** (consigliato) e/o **ntfy** (app push, senza registrazione).

## 1. Configura le notifiche

### Telegram (consigliato)

1. Su Telegram apri [@BotFather](https://t.me/BotFather), scrivi `/newbot` e segui le istruzioni: ricevi un **token**.
2. Apri la chat con il bot appena creato e scrivigli un messaggio qualsiasi (es. "ciao").
3. Ricava il tuo **chat_id**:
   ```bash
   TELEGRAM_BOT_TOKEN="il-tuo-token" python tesla_monitor.py --get-chat-id
   ```

### ntfy (alternativa)

Installa l'app [ntfy](https://ntfy.sh) sul telefono e iscriviti a un argomento con un nome difficile da indovinare
(es. `tesla-mirko-8f3k2`). Usa quel nome come `ntfy_topic`.

## 2. Scegli dove farlo girare

### Opzione A — sul tuo PC / Raspberry Pi (più affidabile)

Tesla blocca spesso le richieste che arrivano da server cloud; da una connessione di casa funziona meglio.

```bash
cd tesla-monitor
cp config.example.json config.json   # poi inserisci token e chat_id
python tesla_monitor.py --test-notify   # verifica che il messaggio arrivi
python tesla_monitor.py --loop          # controlla ogni ora finché il programma resta aperto
```

Serve Python 3.8+ e nient'altro. Se Tesla risponde "403 / Access Denied", installa anche il browser di riserva:

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

Invece di `--loop` puoi far partire un singolo controllo ogni ora con lo scheduler del sistema:

- **Linux / macOS / Raspberry** (`crontab -e`):
  ```
  0 * * * * cd /percorso/tesla-monitor && /usr/bin/python3 tesla_monitor.py >> monitor.log 2>&1
  ```
- **Windows**: Utilità di pianificazione → Crea attività → Attivazione "Ogni giorno, ripeti ogni 1 ora" →
  Azione `python` con argomento `tesla_monitor.py` e "Inizia in" la cartella `tesla-monitor`.

### Opzione B — GitHub Actions (gratis, senza PC acceso)

Il workflow [`.github/workflows/tesla-monitor.yml`](../.github/workflows/tesla-monitor.yml) esegue il controllo ogni ora.

1. Su GitHub: **Settings → Secrets and variables → Actions → New repository secret** e aggiungi
   `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID` (e/o `NTFY_TOPIC`).
2. Il workflow deve trovarsi sul **branch predefinito** del repository: GitHub esegue le pianificazioni solo da lì.
3. Vai su **Actions → Tesla monitor → Run workflow** per provarlo subito.

> ⚠️ I server di GitHub hanno IP da datacenter che Tesla può bloccare. Se il primo avvio manuale fallisce con
> "403", usa l'opzione A. Se funziona, sei a posto: le auto già viste vengono ricordate tra un'esecuzione e l'altra.

## Cambiare i filtri

I filtri sono in cima a `tesla_monitor.py`:

| Variabile | Valore attuale | Significato |
|---|---|---|
| `YEARS` | `{2023}` | anni accettati |
| `TRIM_CODES` / `TRIM_NAME_KEYWORDS` | `M3RWD` / "trazione posteriore" | versione |
| `PAINTS` | `WHITE`, `BLACK` | colori |
| `ZIP`, `RANGE_KM` | `90146`, `0` | CAP e raggio (0 = tutta Italia) |

Le auto già notificate sono salvate in `state.json`: cancellalo per ripartire da zero.

## Opzioni

```
python tesla_monitor.py [--loop] [--interval MINUTI] [--state FILE] [--test-notify] [--get-chat-id]
```

Token e chat_id si possono passare anche come variabili d'ambiente:
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `NTFY_TOPIC`, `NTFY_SERVER`.
