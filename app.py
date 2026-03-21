from flask import Flask, request, jsonify
import requests
import time
import os
import json

app = Flask(__name__)

# ─── CREDENZIALI POSTE ─────────────────────────────────────────────────────────
POSTE_CLIENT_ID = os.environ.get("POSTE_CLIENT_ID", "")
POSTE_SECRET_ID = os.environ.get("POSTE_SECRET_ID", "")
POSTE_COST_CENTER = "CDC-00080197"

MITTENTE = {
    "zipCode": "10070", "streetNumber": "30", "city": "VALLO TORINESE",
    "address": "Via Torino", "country": "ITA1", "countryName": "Italia",
    "nameSurname": "PROREUSE SRLS", "contactName": "PROREUSE SRLS",
    "province": "TO", "email": "proreuse1622@gmail.com",
    "phone": "", "cellphone": "", "note1": "", "note2": ""
}

AUTH_URL = "https://apiw.gp.posteitaliane.it/gp/internet/user/sessions"
WAYBILL_URL = "https://apiw.gp.posteitaliane.it/gp/internet/postalandlogistics/parcel/waybill"
TRACKING_URL = "https://apiw.gp.posteitaliane.it/gp/internet/postalandlogistics/parcel/tracking"
SCOPE_PRODUZIONE = "https://postemarketplace.onmicrosoft.com/d6a78063-5570-4a87-bbd7-07326e6855d1/.default"

# ─── CREDENZIALI SHOPIFY ───────────────────────────────────────────────────────
SHOPIFY_TOKEN = os.environ.get("SHOPIFY_TOKEN", "")
SHOPIFY_SHOP = os.environ.get("SHOPIFY_SHOP", "")
SHOPIFY_API_VERSION = "2026-01"

# ─── CACHE TOKEN ───────────────────────────────────────────────────────────────
_token_cache = {"access_token": None, "expires_at": 0}

ORDINI_FILE = "/tmp/ordini_processati.json"

def carica_ordini():
    try:
        with open(ORDINI_FILE, "r") as f:
            return set(json.load(f))
    except:
        return set()

def salva_ordini(ordini):
    try:
        with open(ORDINI_FILE, "w") as f:
            json.dump(list(ordini), f)
    except:
        pass

# ─── MAPPA PAESI → ZONA ────────────────────────────────────────────────────────
PAESE_ZONA = {
    # Zona 1
    "DE": 1, "NL": 1, "PL": 1, "EE": 1, "LV": 1, "LT": 1, "LI": 1,
    # Zona 2
    "AT": 2, "BE": 2, "DK": 2, "FR": 2, "FI": 2, "LU": 2, "SE": 2,
    "PT": 2, "CZ": 2, "RO": 2, "SK": 2, "SI": 2, "ES": 2, "HU": 2, "MC": 2,
    # Zona 3
    "BG": 3, "HR": 3, "GR": 3, "MT": 3, "NO": 3, "CH": 3,
    # Zona 4
    "CY": 4, "IE": 4,
    # Zona 10
    "GB": 10,
}

# ─── TARIFFE INTERNAZIONALI HD (centesimi) ─────────────────────────────────────
# Prezzo per fascia di peso (uso il prezzo del kg massimo della fascia)
# Fasce: 0-5, 5-10, 10-15, 15-20, 20-25, 25-30
# Oltre 30: prezzo 30kg + (kg eccedenti * tariffa_al_kg)

TARIFFE_INT = {
    1: {
        "fasce": [
            (5,   1260),   # 0-5kg → €12,60
            (10,  1638),   # 5-10kg → €16,38
            (15,  2270),   # 10-15kg → €22,70
            (20,  2650),   # 15-20kg → €26,50
            (25,  3150),   # 20-25kg → €31,50
            (30,  3700),   # 25-30kg → €37,00
        ],
        "base30": 3700,    # €37,00
        "per_kg": 66,      # €0,66 per kg oltre 30
    },
    2: {
        "fasce": [
            (5,   1355),
            (10,  1775),
            (15,  2528),
            (20,  3012),
            (25,  3496),
            (30,  3926),
        ],
        "base30": 3926,
        "per_kg": 70,
    },
    3: {
        "fasce": [
            (5,   2055),
            (10,  2715),
            (15,  3465),
            (20,  4030),
            (25,  4595),
            (30,  5165),
        ],
        "base30": 5165,
        "per_kg": 77,
    },
    4: {
        "fasce": [
            (5,   2501),
            (10,  3387),
            (15,  3933),
            (20,  4348),
            (25,  4839),
            (30,  5331),
        ],
        "base30": 5331,
        "per_kg": 166,
    },
    10: {
        "fasce": [
            (5,   2028),
            (10,  2406),
            (15,  2874),
            (20,  3252),
            (25,  3961),
            (30,  4501),
        ],
        "base30": 4501,
        "per_kg": 70,
    },
}

def calcola_prezzo_internazionale(zona, peso_kg):
    """Calcola il prezzo per spedizione internazionale in centesimi"""
    if zona not in TARIFFE_INT:
        return None

    t = TARIFFE_INT[zona]

    if peso_kg <= 30:
        for (limite, prezzo) in t["fasce"]:
            if peso_kg <= limite:
                return prezzo
        return t["base30"]
    else:
        # Oltre 30kg: fascia di 5 in 5 fino a 300kg
        kg_eccedenti = peso_kg - 30
        prezzo = t["base30"] + int(kg_eccedenti * t["per_kg"])
        return prezzo


# ─── TARIFFE ITALIA (centesimi) ────────────────────────────────────────────────
def calcola_prezzo_italia(peso_kg):
    if peso_kg <= 2:
        return 430
    elif peso_kg <= 5:
        return 500
    elif peso_kg <= 10:
        return 600
    elif peso_kg <= 20:
        return 700
    elif peso_kg <= 30:
        return 830
    elif peso_kg <= 50:
        return 1460
    elif peso_kg <= 70:
        return 1600
    elif peso_kg <= 100:
        return 1940
    elif peso_kg <= 200:
        return 3880
    elif peso_kg <= 300:
        return 5820
    elif peso_kg <= 400:
        return 7760
    elif peso_kg <= 500:
        return 9700
    else:
        return 9700


# ─── FUNZIONI POSTE ────────────────────────────────────────────────────────────

def get_poste_token():
    now = time.time()
    if _token_cache["access_token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["access_token"]
    payload = {
        "clientId": POSTE_CLIENT_ID, "secretId": POSTE_SECRET_ID,
        "scope": SCOPE_PRODUZIONE, "grantType": "client_credentials"
    }
    headers = {"POSTE_clientID": POSTE_CLIENT_ID, "Content-Type": "application/json"}
    resp = requests.post(AUTH_URL, json=payload, headers=headers, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    _token_cache["access_token"] = data["access_token"]
    _token_cache["expires_at"] = now + data.get("expires_in", 3599)
    return _token_cache["access_token"]


def crea_spedizione_poste(ordine, paperless=False):
    try:
        token = get_poste_token()
        shipping = ordine.get("shipping_address", {})
        paese = shipping.get("country_code", "IT").upper()

        peso_grammi = sum(
            item.get("grams", 500) * item.get("quantity", 1)
            for item in ordine.get("line_items", [])
        )
        peso_kg = max(1, round(peso_grammi / 1000))

        # Determina prodotto e paese destinatario
        if paese == "IT":
            product_code = "APT000901"  # Express Italia
            country_code = "ITA1"
            country_name = "Italia"
        else:
            product_code = "APT001013"  # International Plus
            # Codice paese ISO per Poste internazionale
            country_code = paese
            country_name = shipping.get("country", "")

        payload = {
            "costCenterCode": POSTE_COST_CENTER,
            "paperless": paperless,
            "shipmentDate": time.strftime("%Y-%m-%dT%H:%M:%S.000+0000", time.gmtime()),
            "waybills": [{
                "clientReferenceId": str(ordine.get("order_number", ordine.get("id", "")))[:25],
                "printFormat": "A4",
                "product": product_code,
                "data": {
                    "declared": [{
                        "weight": str(peso_kg * 1000),
                        "height": "10", "length": "30", "width": "25"
                    }],
                    "content": "Merce varia",
                    "services": {},
                    "sender": MITTENTE,
                    "receiver": {
                        "zipCode": shipping.get("zip", ""),
                        "addressId": "",
                        "streetNumber": "",
                        "city": shipping.get("city", "").upper(),
                        "address": shipping.get("address1", ""),
                        "country": country_code,
                        "countryName": country_name,
                        "nameSurname": f"{shipping.get('first_name', '')} {shipping.get('last_name', '')}".strip(),
                        "contactName": f"{shipping.get('first_name', '')} {shipping.get('last_name', '')}".strip(),
                        "province": shipping.get("province_code", "")[:2].upper() if paese == "IT" else "",
                        "email": ordine.get("email", ""),
                        "phone": shipping.get("phone", ""),
                        "cellphone": "", "note1": "", "note2": ""
                    }
                }
            }]
        }

        headers = {
            "POSTE_clientID": POSTE_CLIENT_ID,
            "Authorization": token,
            "Content-Type": "application/json"
        }
        resp = requests.post(WAYBILL_URL, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        result = resp.json()
        print(f"RISPOSTA POSTE: {result}")
        ldv = result.get("waybills", [{}])[0].get("code", "")
        print(f"Spedizione creata! LDV: {ldv} - Paese: {paese}")
        return ldv
    except Exception as e:
        print(f"Errore creazione spedizione Poste: {e}")
        return None


def get_tracking_poste(ldv):
    try:
        token = get_poste_token()
        payload = {
            "arg0": {
                "shipmentsData": [{"waybillNumber": ldv, "lastTracingState": "S"}],
                "statusDescription": "E", "customerType": "DQ"
            }
        }
        headers = {
            "POSTE_clientID": POSTE_CLIENT_ID,
            "Authorization": token,
            "Content-Type": "application/json"
        }
        resp = requests.post(TRACKING_URL, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        shipment = result.get("return", {}).get("messages", [{}])[0]
        tracking_events = shipment.get("tracking", [])
        if tracking_events:
            ultimo = tracking_events[-1]
            stato = ultimo.get("statusDescription", "")
            data = ultimo.get("data", "")
            print(f"Tracking {ldv}: {stato} ({data})")
            return stato
        return None
    except Exception as e:
        print(f"Errore tracking Poste: {e}")
        return None


def aggiorna_tracking_shopify(ordine_id, order_number, ldv):
    try:
        headers = {
            "X-Shopify-Access-Token": SHOPIFY_TOKEN,
            "Content-Type": "application/json"
        }
        url_fo = f"https://{SHOPIFY_SHOP}/admin/api/{SHOPIFY_API_VERSION}/orders/{ordine_id}/fulfillment_orders.json"
        resp_fo = requests.get(url_fo, headers=headers, timeout=10)
        resp_fo.raise_for_status()
        fulfillment_orders = resp_fo.json().get("fulfillment_orders", [])
        if not fulfillment_orders:
            return False
        line_items_by_fulfillment = [
            {"fulfillment_order_id": fo["id"]}
            for fo in fulfillment_orders
            if fo.get("status") in ("open", "in_progress")
        ]
        if not line_items_by_fulfillment:
            return False
        url_f = f"https://{SHOPIFY_SHOP}/admin/api/{SHOPIFY_API_VERSION}/fulfillments.json"
        payload = {
            "fulfillment": {
                "line_items_by_fulfillment_order": line_items_by_fulfillment,
                "tracking_info": {
                    "company": "Poste Italiane", "number": ldv,
                    "url": f"https://www.poste.it/cerca/index.html#!/cerca/ricerca-spedizioni/{ldv}"
                },
                "notify_customer": True
            }
        }
        resp = requests.post(url_f, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        print(f"Tracking aggiornato su Shopify per ordine #{order_number}: {ldv}")
        return True
    except Exception as e:
        print(f"Errore aggiornamento tracking Shopify: {e}")
        return False


# ─── ROUTE ─────────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    return "OK", 200


@app.route("/shipping-rates", methods=["POST"])
def shipping_rates():
    data = request.get_json(silent=True)

    peso_grammi = 0
    paese = "IT"

    if data and "rate" in data:
        rate = data["rate"]
        paese = rate.get("destination", {}).get("country", "IT").upper()
        for item in rate.get("items", []):
            peso_grammi += item.get("grams", 500) * item.get("quantity", 1)

    peso_kg = max(0.1, peso_grammi / 1000)

    if paese == "IT":
        prezzo = calcola_prezzo_italia(peso_kg)
        nome_servizio = "Poste Italiane Express (1-2 giorni)"
    else:
        zona = PAESE_ZONA.get(paese)
        if zona is None:
            # Paese non gestito da Poste
            return jsonify({"rates": []}), 200
        prezzo = calcola_prezzo_internazionale(zona, peso_kg)
        if prezzo is None:
            return jsonify({"rates": []}), 200
        nome_servizio = f"Poste Italiane International Plus (Zona {zona})"

    return jsonify({
        "rates": [{
            "service_name": nome_servizio,
            "service_code": "poste_express",
            "total_price": str(prezzo),
            "currency": "EUR",
            "min_delivery_date": None,
            "max_delivery_date": None
        }]
    }), 200


@app.route("/tracking/<ldv>", methods=["GET"])
def tracking(ldv):
    stato = get_tracking_poste(ldv)
    if stato:
        return jsonify({"ldv": ldv, "stato": stato}), 200
    else:
        return jsonify({"error": "Tracking non disponibile"}), 404


@app.route("/webhook/order-created", methods=["POST"])
def order_created():
    return jsonify({"status": "ok"}), 200


@app.route("/webhook/order-fulfilled", methods=["POST"])
def order_fulfilled():
    ordine = request.get_json(silent=True)
    if not ordine:
        return "Bad Request", 400

    ordine_id = str(ordine.get("id", ""))
    order_number = ordine.get("order_number", "")

    ordini_processati = carica_ordini()
    if ordine_id in ordini_processati:
        print(f"Ordine #{order_number} gia processato, ignoro duplicato")
        return jsonify({"status": "ok", "message": "already processed"}), 200

    ordini_processati.add(ordine_id)
    salva_ordini(ordini_processati)
    shipping = ordine.get("shipping_address", {})
    paese = shipping.get("country_code", "IT").upper()

    # Controlla se il paese è gestito da Poste
    if paese != "IT" and paese not in PAESE_ZONA:
        print(f"Paese {paese} non gestito da Poste, ignoro")
        return jsonify({"status": "ok", "message": "paese non gestito da Poste"}), 200

    print(f"Ordine evaso: #{order_number} - {ordine.get('email', '')} - {paese}")

    ldv = crea_spedizione_poste(ordine, paperless=False)

    if ldv:
        aggiorna_tracking_shopify(ordine_id, order_number, ldv)
        return jsonify({"status": "ok", "ldv": ldv}), 200
    else:
        return jsonify({"status": "error", "message": "Errore creazione spedizione"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
