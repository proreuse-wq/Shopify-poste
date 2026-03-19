from flask import Flask, request, jsonify
import requests
import time
import os
import json

app = Flask(__name__)

# ─── CREDENZIALI POSTE DELIVERY BUSINESS ───────────────────────────────────────
POSTE_CLIENT_ID = os.environ.get("POSTE_CLIENT_ID", "")
POSTE_SECRET_ID = os.environ.get("POSTE_SECRET_ID", "")

# Codice centro di costo
POSTE_COST_CENTER = "CDC-00080197"

# Dati mittente
MITTENTE = {
    "zipCode": "10070",
    "streetNumber": "30",
    "city": "VALLO TORINESE",
    "address": "Via Torino",
    "country": "ITA1",
    "countryName": "Italia",
    "nameSurname": "PROREUSE SRLS",
    "contactName": "PROREUSE SRLS",
    "province": "TO",
    "email": "proreuse1622@gmail.com",
    "phone": "",
    "cellphone": "",
    "note1": "",
    "note2": ""
}

# ─── URL API POSTE ──────────────────────────────────────────────────────────────
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

# ─── ANTI-DUPLICATI PERSISTENTE ────────────────────────────────────────────────
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


def get_poste_token():
    now = time.time()
    if _token_cache["access_token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["access_token"]
    payload = {
        "clientId": POSTE_CLIENT_ID,
        "secretId": POSTE_SECRET_ID,
        "scope": SCOPE_PRODUZIONE,
        "grantType": "client_credentials"
    }
    headers = {"POSTE_clientID": POSTE_CLIENT_ID, "Content-Type": "application/json"}
    resp = requests.post(AUTH_URL, json=payload, headers=headers, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    _token_cache["access_token"] = data["access_token"]
    _token_cache["expires_at"] = now + data.get("expires_in", 3599)
    return _token_cache["access_token"]


def crea_spedizione_poste(ordine, paperless=True):
    try:
        token = get_poste_token()
        shipping = ordine.get("shipping_address", {})
        peso_grammi = sum(
            item.get("grams", 500) * item.get("quantity", 1)
            for item in ordine.get("line_items", [])
        )
        peso_kg = max(1, round(peso_grammi / 1000))
        payload = {
            "costCenterCode": POSTE_COST_CENTER,
            "paperless": paperless,
            "shipmentDate": time.strftime("%Y-%m-%dT%H:%M:%S.000+0000", time.gmtime()),
            "waybills": [{
                "clientReferenceId": str(ordine.get("order_number", ordine.get("id", "")))[:25],
                "printFormat": "A4",
                "product": "APT000901",
                "data": {
                    "declared": [{
                        "weight": str(peso_kg * 1000),
                        "height": "10",
                        "length": "30",
                        "width": "25"
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
                        "country": "ITA1",
                        "countryName": "Italia",
                        "nameSurname": f"{shipping.get('first_name', '')} {shipping.get('last_name', '')}".strip(),
                        "contactName": f"{shipping.get('first_name', '')} {shipping.get('last_name', '')}".strip(),
                        "province": shipping.get("province_code", "")[:2].upper(),
                        "email": ordine.get("email", ""),
                        "phone": shipping.get("phone", ""),
                        "cellphone": "",
                        "note1": "",
                        "note2": ""
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
        modo = "BOZZA" if paperless else "con etichetta"
        print(f"Spedizione creata {modo}! LDV: {ldv}")
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
                "statusDescription": "E",
                "customerType": "DQ"
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
            print(f"Nessun fulfillment order per ordine #{order_number}")
            return False
        line_items_by_fulfillment = [
            {"fulfillment_order_id": fo["id"]}
            for fo in fulfillment_orders
            if fo.get("status") in ("open", "in_progress")
        ]
        if not line_items_by_fulfillment:
            print(f"Nessun fulfillment order aperto per ordine #{order_number}")
            return False
        url_f = f"https://{SHOPIFY_SHOP}/admin/api/{SHOPIFY_API_VERSION}/fulfillments.json"
        payload = {
            "fulfillment": {
                "line_items_by_fulfillment_order": line_items_by_fulfillment,
                "tracking_info": {
                    "company": "Poste Italiane",
                    "number": ldv,
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
    if data and "rate" in data:
        for item in data["rate"].get("items", []):
            peso_grammi += item.get("grams", 500) * item.get("quantity", 1)
    peso_kg = peso_grammi / 1000

    if peso_kg <= 2:
        prezzo = 430
    elif peso_kg <= 5:
        prezzo = 500
    elif peso_kg <= 10:
        prezzo = 600
    elif peso_kg <= 20:
        prezzo = 700
    elif peso_kg <= 30:
        prezzo = 830
    elif peso_kg <= 50:
        prezzo = 1460
    elif peso_kg <= 70:
        prezzo = 1600
    elif peso_kg <= 100:
        prezzo = 1940
    elif peso_kg <= 200:
        prezzo = 3880
    elif peso_kg <= 300:
        prezzo = 5820
    elif peso_kg <= 400:
        prezzo = 7760
    elif peso_kg <= 500:
        prezzo = 9700
    else:
        prezzo = 9700

    return jsonify({
        "rates": [{
            "service_name": "Poste Italiane Express (1-2 giorni)",
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
    print(f"Ordine evaso: #{order_number} - {ordine.get('email', '')}")

    ldv = crea_spedizione_poste(ordine, paperless=True)

    if ldv:
        aggiorna_tracking_shopify(ordine_id, order_number, ldv)
        return jsonify({"status": "ok", "ldv": ldv}), 200
    else:
        return jsonify({"status": "error", "message": "Errore creazione spedizione"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
