"""Genera i fixture di esempio usati dai test e dalla demo offline.

I dati riproducono la forma della risposta dell'API Tesla (campi, codici
optional, blocco specifiche) con valori inventati ma plausibili.
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

PAINTS = {
    "$PPSW": "Bianco Perla Multistrato",
    "$PBSB": "Nero Pastello",
    "$PPMR": "Rosso Multistrato",
    "$PPSB": "Blu Metallizzato",
    "$PMNG": "Grigio Midnight Silver",
}
WHEELS = {
    "$W38B": 'Cerchi Aero da 18"',
    "$W39B": 'Cerchi Sport da 19"',
    "$W41B": 'Cerchi Uberturbine da 20"',
}
INTERIORS = {"$IBB1": "Interni Neri", "$IWW1": "Interni Bianchi"}
TRIMS = {
    "RWD": "Trazione Posteriore",
    "LR": "Long Range All-Wheel Drive",
    "PERF": "Performance All-Wheel Drive",
}
SPECS = {
    "RWD": ("491", "6.1", "225"),
    "LR": ("614", "4.4", "233"),
    "PERF": ("547", "3.3", "261"),
}


def car(vin, year, trim, odometer, price, *, paint="$PPSW", wheels="$W38B",
        interior="$IBB1", fsd=False, eap=False, tow=False, damage=False,
        delivery=None, city="Palermo", fees=0, drop_price=False):
    codes = [paint, interior, wheels]
    options = [
        {"code": paint, "group": "PAINT", "name": PAINTS[paint]},
        {"code": interior, "group": "INTERIOR", "name": INTERIORS[interior]},
        {"code": wheels, "group": "WHEELS", "name": WHEELS[wheels]},
    ]
    if fsd:
        codes.append("$APF2")
        options.append({"code": "$APF2", "group": "AUTOPILOT",
                        "name": "Capacita di Guida Autonoma Totale"})
    if eap:
        codes.append("$APBS")
        options.append({"code": "$APBS", "group": "AUTOPILOT",
                        "name": "Autopilot Avanzato"})
    if tow:
        codes.append("$TW01")
        options.append({"code": "$TW01", "group": "TOWING",
                        "name": "Gancio di traino"})

    range_km, accel, top_speed = SPECS[trim]
    record = {
        "VIN": vin,
        "Model": "m3",
        "Year": year,
        "TrimName": TRIMS[trim],
        "Odometer": odometer,
        "OdometerType": "KM",
        "CurrencyCode": "EUR",
        "City": city,
        "StateProvince": "PA",
        "CountryCode": "IT",
        "TitleStatus": "USED",
        "IsDemo": False,
        "HasDamagePhotos": damage,
        "OriginalDeliveryDate": delivery or f"{year}-05-12T00:00:00",
        "OptionCodeList": ",".join(codes),
        "OptionCodeData": options,
        "OptionCodeSpecs": {
            "C_SPECS": {
                "code": "C_SPECS",
                "name": "Specifiche",
                "options": [
                    {"code": "SPECS_RANGE", "name": "Autonomia (WLTP)",
                     "value": range_km, "unit_short": "km"},
                    {"code": "SPECS_ACCELERATION", "name": "Accelerazione 0-100 km/h",
                     "value": accel, "unit_short": "s"},
                    {"code": "SPECS_TOPSPEED", "name": "Velocita massima",
                     "value": top_speed, "unit_short": "km/h"},
                ],
            }
        },
    }
    if not drop_price:
        record.update({"Price": price, "InventoryPrice": price,
                       "PurchasePrice": price, "TotalPrice": price + fees})
    if fees:
        record["TransportFee"] = fees
    return record


CARS = [
    car("LRW3E7EA1KC100001", 2019, "RWD", 118000, 18900, paint="$PBSB"),
    car("LRW3E7EB2LC100002", 2020, "LR", 95000, 22500, eap=True, paint="$PMNG"),
    car("LRW3E7EK3MC100003", 2021, "LR", 62000, 28900, eap=True, tow=True,
        wheels="$W39B"),
    car("LRW3E7EC4MC100004", 2021, "PERF", 71000, 31500, wheels="$W41B",
        paint="$PPMR", interior="$IWW1"),
    car("LRW3E7EA5NC100005", 2022, "RWD", 38000, 27900),
    car("LRW3E7EK6NC100006", 2022, "LR", 45000, 32900, fsd=True, wheels="$W39B",
        paint="$PPSB"),
    car("LRW3E7EA7PC100007", 2023, "RWD", 29000, 29900, paint="$PBSB"),
    car("LRW3E7EK8RC100008", 2024, "LR", 21000, 39900, wheels="$W39B", fees=300),
    car("LRW3E7EC9PC100009", 2023, "PERF", 48000, 38500, damage=True,
        wheels="$W41B"),
    car("LRW3E7EA0LC100010", 2020, "RWD", 140000, 17400, paint="$PMNG"),
    # Annuncio senza prezzo: serve a verificare che venga escluso con una nota.
    car("LRW3E7EA1MC100011", 2021, "RWD", 55000, 0, drop_price=True),
]


def payload(cars):
    return {"results": cars, "total_matches_found": str(len(cars))}


def main():
    (HERE / "inventario_esempio.json").write_text(
        json.dumps(payload(CARS), ensure_ascii=False, indent=2), encoding="utf-8")

    # Seconda fotografia: un ribasso, un rincaro, un'auto venduta, una nuova.
    later = [dict(c) for c in CARS]
    for record in later:
        if record["VIN"] == "LRW3E7EK3MC100003":
            for key in ("Price", "InventoryPrice", "PurchasePrice", "TotalPrice"):
                record[key] = 26900
        if record["VIN"] == "LRW3E7EA5NC100005":
            for key in ("Price", "InventoryPrice", "PurchasePrice", "TotalPrice"):
                record[key] = 28400
    later = [r for r in later if r["VIN"] != "LRW3E7EA0LC100010"]
    later.append(car("LRW3E7EK2SC100012", 2023, "LR", 33000, 30900, fsd=True,
                     wheels="$W39B", interior="$IWW1"))

    (HERE / "inventario_esempio_dopo.json").write_text(
        json.dumps(payload(later), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"scritti {len(CARS)} + {len(later)} annunci di esempio")


if __name__ == "__main__":
    main()
