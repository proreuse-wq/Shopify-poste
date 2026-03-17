from flask import Flask, request, jsonify
import requests
import time

app = Flask(__name__)

# ─── CREDENZIALI POSTE DELIVERY BUSINESS ───────────────────────────────────────
POSTE_CLIENT_ID = "842f24cd-7e53-43cb-8a6f-a7930afda146"   # il tuo CLIENT ID
POSTE_SECRET_ID = "2S78Q-Z4JMyQJ1JejV0-SgYsywRUrC2ScvmOcFD"  # il tuo SECRET ID

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

# ─── CACHE TOKEN ───────────────────────────────────────────────────────────────
_token_cache = {"access_token": None, "expires_at": 0}


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
                "clientReferenceId": str(ordine.get("id", ""))[:25],
                "printFormat": "A4",
                "product": "APT000902",  # Standard (usa APT000901 per Express)
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

        headers = {
            "POSTE_clientID": POSTE_CLIENT_ID,
            "Authorization": token,
            "Content-Type": "application/json"
        }

        resp = requests.post(WAYBILL_URL, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        result = resp.json()

        # Estrai numero LDV dalla risposta
        ldv = result.get("waybills", [{}])[0].get("waybillNumber", "")
        print(f"✅ Spedizione creata! LDV: {ldv}")
        return ldv

    except Exception as e:
        print(f"❌ Errore creazione spedizione Poste: {e}")
        return None


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

    # Tariffe basate sul peso (adatta ai tuoi prezzi contrattuali)
    if peso_grammi <= 2000:
        standard_price = 490   # €4.90
        express_price = 790    # €7.90
    elif peso_grammi <= 5000:
        standard_price = 590
        express_price = 990
    else:
        standard_price = 790
        express_price = 1290

    return jsonify({
        "rates": [
            {
                "service_name": "Poste Italiane Standard (4-5 giorni)",
                "service_code": "poste_standard",
                "total_price": str(standard_price),
                "currency": "EUR",
                "min_delivery_date": None,
                "max_delivery_date": None
            },
            {
                "service_name": "Poste Italiane Express (1-2 giorni)",
                "service_code": "poste_express",
                "total_price": str(express_price),
                "currency": "EUR",
                "min_delivery_date": None,
                "max_delivery_date": None
            }
        ]
    }), 200


@app.route("/webhook/order-created", methods=["POST"])
def order_created():
    """
    Webhook chiamato da Shopify quando viene creato un nuovo ordine.
    Crea automaticamente la spedizione su Poste Delivery Business.
    """
    ordine = request.get_json(silent=True)
    if not ordine:
        return "Bad Request", 400

    print(f"📦 Nuovo ordine ricevuto: #{ordine.get('order_number', '')} - {ordine.get('email', '')}")

    ldv = crea_spedizione_poste(ordine)

    if ldv:
        return jsonify({"status": "ok", "ldv": ldv}), 200
    else:
        return jsonify({"status": "error", "message": "Errore creazione spedizione"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
