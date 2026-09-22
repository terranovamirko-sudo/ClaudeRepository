#!/usr/bin/env bash
# Avvia il controllo ogni 3 ore, ripartendo da solo se il processo muore.
# Uso:  ./esegui.sh            (controllo continuo)
#       ./esegui.sh --once     (una sola analisi)
set -euo pipefail

cd "$(dirname "$0")"

CONFIG=()
[[ -f config.json ]] && CONFIG=(--config config.json)

if [[ "${1:-}" == "--once" ]]; then
  exec python3 -m tesla_inventory "${CONFIG[@]}"
fi

while true; do
  python3 -m tesla_inventory "${CONFIG[@]}" --loop || {
    echo "Processo terminato con codice $? — riprovo fra 5 minuti." >&2
    sleep 300
  }
done
