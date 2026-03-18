from flask import Flask, request, jsonify
import requests
import time
import os

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
    "phone": "",        # <-- aggiungi il tuo telefono se vuoi
    "cellphone": "",
    "note1": "",
    "note2": ""
}

# ─── URL API POSTE ──────────────────────────────────────────────────────────────
AUTH_URL = "https://apiw.gp.posteitaliane.it/gp/internet/user/sessions"
WAYBILL_URL = "https://apiw.gp.posteitaliane.it/gp/internet/postalandlogistics/parcel/waybill"
SCOPE_PRODUZIONE = "https://postemarketplace.onmicrosoft.com/d6a78063-5570-4a87-bbd7-07326e6855d1/.default"

# ─── CREDENZIALI SHOPIFY ───────────────────────────────────────────────────────
SHOPIFY_TOKEN = os.environ.get("SHOPIFY_TOKEN", "")
SHOPIFY_SHOP = os.environ.get("SHOPIFY_SHOP", "")
SHOPIFY_API_VERSION = "2026-01"

# ─── CACHE TOKEN ───────────────────────────────────────────────────────────────
_token_cache = {"access_token": None, "expires_at": 0}

# ─── ORDINI GIA PROCESSATI (anti-duplicati persistente) ────────────────────────
import json

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
    """Ottieni token Poste (con cache di 1 ora)"""
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


def crea_spedizione_poste(ordine):
    """
    Crea una spedizione su Poste Delivery Business.
    ordine: dict con i dati dell'ordine Shopify (shipping_address, peso, ecc.)
    Ritorna il numero LDV o None in caso di errore.
    """
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
            "paperless": False,
            "shipmentDate": time.strftime("%Y-%m-%dT%H:%M:%S.000+0000", time.gmtime()),
            "waybills": [{
                "clientReferenceId": str(ordine.get("order_number", ordine.get("id", "")))[:25],
                "printFormat": "A4",
                "product": "APT000901",  # Express
                "data": {
                    "declared": [{
                        "weight": str(peso_kg * 1000),  # grammi
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

        print(f"PAYLOAD INVIATO A POSTE: {payload}")
        headers = {
            "POSTE_clientID": POSTE_CLIENT_ID,
            "Authorization": token,
            "Content-Type": "application/json"
        }

        resp = requests.post(WAYBILL_URL, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        result = resp.json()

        # Estrai numero LDV dalla risposta
        print(f"RISPOSTA POSTE COMPLETA: {result}")
        ldv = result.get("waybills", [{}])[0].get("code", "")
        print(f"✅ Spedizione creata! LDV: {ldv}")
        return ldv

    except Exception as e:
        print(f"❌ Errore creazione spedizione Poste: {e}")
        return None


def aggiorna_tracking_shopify(ordine_id, order_number, ldv):
    """Aggiorna il tracking dell ordine su Shopify con il numero LDV di Poste"""
    try:
        # Prima crea il fulfillment
        url = f"https://{SHOPIFY_SHOP}/admin/api/{SHOPIFY_API_VERSION}/orders/{ordine_id}/fulfillments.json"
        headers = {
            "X-Shopify-Access-Token": SHOPIFY_TOKEN,
            "Content-Type": "application/json"
        }
        payload = {
            "fulfillment": {
                "tracking_company": "Poste Italiane",
                "tracking_number": ldv,
                "tracking_url": f"https://www.poste.it/cerca/index.html#!/cerca/ricerca-spedizioni/{ldv}",
                "notify_customer": True
            }
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        print(f"✅ Tracking aggiornato su Shopify per ordine #{order_number}: {ldv}")
        return True
    except Exception as e:
        print(f"❌ Errore aggiornamento tracking Shopify: {e}")
        return False


# ─── ROUTE SHOPIFY ─────────────────────────────────────────────────────────────

@app.route("/")
def home():
    return "OK", 200


@app.route("/shipping-rates", methods=["POST"])
def shipping_rates():
    """
    Endpoint chiamato da Shopify al checkout per mostrare le tariffe di spedizione.
    Restituisce tariffe fisse basate sul peso dell'ordine.
    """
    data = request.get_json(silent=True)
    print("SHOPIFY RATES REQUEST:", data)

    # Calcola peso totale in grammi
    peso_grammi = 0
    if data and "rate" in data:
        for item in data["rate"].get("items", []):
            peso_grammi += item.get("grams", 500) * item.get("quantity", 1)

    peso_kg = peso_grammi / 1000

    # Tariffe reali contratto Poste Delivery Business Express (a domicilio)
    if peso_kg <= 2:
        prezzo = 424      # €4,24
    elif peso_kg <= 5:
        prezzo = 499      # €4,99
    elif peso_kg <= 10:
        prezzo = 603      # €6,03
    elif peso_kg <= 20:
        prezzo = 703      # €7,03
    elif peso_kg <= 30:
        prezzo = 828      # €8,28
    elif peso_kg <= 50:
        prezzo = 1456     # €14,56
    elif peso_kg <= 70:
        prezzo = 1596     # €15,96
    elif peso_kg <= 100:
        prezzo = 1940     # €19,40
    elif peso_kg <= 200:
        prezzo = 1940 + 1940      # €38,80
    elif peso_kg <= 300:
        prezzo = 1940 + 1940 * 2  # €58,20
    elif peso_kg <= 400:
        prezzo = 1940 + 1940 * 3  # €77,60
    elif peso_kg <= 500:
        prezzo = 1940 + 1940 * 4  # €97,00
    else:
        prezzo = 1940 + 1940 * 4  # oltre 500kg stesso prezzo

    return jsonify({
        "rates": [
            {
                "service_name": "Poste Italiane Express (1-2 giorni)",
                "service_code": "poste_express",
                "total_price": str(prezzo),
                "currency": "EUR",
                "min_delivery_date": None,
                "max_delivery_date": None
            }
        ]
    }), 200


@app.route("/webhook/order-created", methods=["POST"])
def order_created():
    """Vecchio webhook mantenuto per compatibilita"""    return jsonify({"status": "ok"}), 200


@app.route("/webhook/order-fulfilled", methods=["POST"])
def order_fulfilled():
    """
    Webhook chiamato da Shopify quando un ordine viene evaso.
    Crea la spedizione su Poste e aggiorna il tracking su Shopify.
    """
    ordine = request.get_json(silent=True)
    if not ordine:
        return "Bad Request", 400

    ordine_id = str(ordine.get("id", ""))
    order_number = ordine.get("order_number", "")

    ordini_processati = carica_ordini()
    if ordine_id in ordini_processati:
        print(f"⚠️ Ordine #{order_number} già processato, ignoro duplicato")
        return jsonify({"status": "ok", "message": "already processed"}), 200

    ordini_processati.add(ordine_id)
    salva_ordini(ordini_processati)
    print(f"📦 Ordine evaso: #{order_number} - {ordine.get('email', '')}")

    ldv = crea_spedizione_poste(ordine)

    if ldv:
        aggiorna_tracking_shopify(ordine_id, order_number, ldv)
        return jsonify({"status": "ok", "ldv": ldv}), 200
    else:
        return jsonify({"status": "error", "message": "Errore creazione spedizione"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
