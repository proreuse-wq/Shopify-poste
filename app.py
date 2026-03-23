from flask import Flask, request, jsonify
import requests
import time
import os
import json

app = Flask(__name__)

# ─── CREDENZIALI POSTE ─────────────────────────────────────────────────────────
POSTE_CLIENT_ID = os.environ.get("POSTE_CLIENT_ID", "")
POSTE_SECRET_ID = os.environ.get("POSTE_SECRET_ID", "")
POSTE_COST_CENTER = os.environ.get("POSTE_COST_CENTER", "CDC-00080197")
POSTE_CONTRACT_CODE = os.environ.get("POSTE_CONTRACT_CODE", "SFA-01121617")
POSTE_SENDER_PHONE = os.environ.get("POSTE_SENDER_PHONE") or os.environ.get("MITTENTE_PHONE") or "+393711998531"
POSTE_DEFAULT_TARIC = os.environ.get("POSTE_DEFAULT_TARIC", "2313200310")
POSTE_PACKAGING_CODE = os.environ.get("POSTE_PACKAGING_CODE", "C")
POSTE_INTL_CONTENT_CODE = os.environ.get("POSTE_INTL_CONTENT_CODE", "999")
DEBUG_POSTE = (os.environ.get("DEBUG_POSTE", "false").lower() == "true")

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
    "phone": POSTE_SENDER_PHONE,
    "cellphone": "",
    "note1": "",
    "note2": "",
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
    except Exception:
        return set()


def salva_ordini(ordini):
    try:
        with open(ORDINI_FILE, "w") as f:
            json.dump(list(ordini), f)
    except Exception:
        pass


PAESE_ZONA = {
    "DE": 1, "NL": 1, "PL": 1, "EE": 1, "LV": 1, "LT": 1, "LI": 1,
    "AT": 2, "BE": 2, "DK": 2, "FR": 2, "FI": 2, "LU": 2, "SE": 2,
    "PT": 2, "CZ": 2, "RO": 2, "SK": 2, "SI": 2, "ES": 2, "HU": 2, "MC": 2,
    "BG": 3, "HR": 3, "GR": 3, "MT": 3, "NO": 3, "CH": 3,
    "CY": 4, "IE": 4,
    "GB": 10,
}

ISO2_TO_ISO4 = {
    "AT": "AUT1", "BE": "BEL1", "BG": "BGR1", "HR": "HRV1",
    "CY": "CYP1", "CZ": "CZE1", "DK": "DNK1", "EE": "EST1",
    "FI": "FIN1", "FR": "FRA1", "DE": "DEU1", "GR": "GRC1",
    "HU": "HUN1", "IE": "IRL1", "IT": "ITA1", "LV": "LVA1",
    "LT": "LTU1", "LU": "LUX1", "MT": "MLT1", "NL": "NLD1",
    "PL": "POL1", "PT": "PRT1", "RO": "ROU1", "SK": "SVK1",
    "SI": "SVN1", "ES": "ESP1", "SE": "SWE1", "GB": "GBR1",
    "NO": "NOR1", "CH": "CHE1", "LI": "LIE1", "MC": "MCO1",
}

TARIFFE_KG = {
    1: [910,1120,1150,1240,1260,1362,1426,1472,1518,1638,1739,1850,1980,2100,2270,2320,2450,2530,2590,2650,2690,2780,2900,3080,3150,3320,3450,3550,3620,3700],
    2: [993,1119,1129,1334,1355,1549,1570,1603,1667,1775,2098,2205,2313,2420,2528,2829,2904,2947,2990,3012,3227,3335,3388,3442,3496,3603,3716,3786,3856,3926],
    3: [1555,1665,1750,1885,2055,2210,2310,2415,2515,2715,3010,3125,3235,3350,3465,3575,3690,3805,3915,4030,4145,4255,4370,4485,4595,4710,4825,4935,5050,5165],
    4: [1737,1901,2065,2469,2501,2633,2829,3048,3157,3387,3627,3703,3769,3867,3933,3955,4053,4151,4250,4348,4446,4544,4643,4741,4839,4938,5036,5134,5233,5331],
    10: [1440,1716,1812,1932,2028,2064,2136,2256,2340,2406,2592,2664,2730,2802,2874,2940,3012,3060,3150,3252,3529,3661,3841,3937,3961,3985,4201,4273,4381,4501],
}
TARIFFA_PER_KG = {1: 66, 2: 70, 3: 77, 4: 166, 10: 70}


def calcola_prezzo_internazionale(zona, peso_kg):
    if zona not in TARIFFE_KG:
        return None
    peso_intero = max(1, int(peso_kg) if peso_kg == int(peso_kg) else int(peso_kg) + 1)
    if peso_intero <= 30:
        return TARIFFE_KG[zona][peso_intero - 1]
    if peso_intero <= 500:
        base30 = TARIFFE_KG[zona][29]
        per_kg = TARIFFA_PER_KG[zona]
        kg_eccedenti = peso_intero - 30
        fascia = ((kg_eccedenti - 1) // 5 + 1) * 5
        return base30 + fascia * per_kg
    return TARIFFE_KG[zona][29] + 470 * TARIFFA_PER_KG[zona]


def calcola_prezzo_italia(peso_kg):
    if peso_kg <= 2:
        return 430
    if peso_kg <= 5:
        return 500
    if peso_kg <= 10:
        return 600
    if peso_kg <= 20:
        return 700
    if peso_kg <= 30:
        return 830
    if peso_kg <= 50:
        return 1460
    if peso_kg <= 70:
        return 1600
    if peso_kg <= 100:
        return 1940
    if peso_kg <= 200:
        return 3880
    if peso_kg <= 300:
        return 5820
    if peso_kg <= 400:
        return 7760
    return 9700


def get_poste_token():
    now = time.time()
    if _token_cache["access_token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["access_token"]
    payload = {
        "clientId": POSTE_CLIENT_ID,
        "secretId": POSTE_SECRET_ID,
        "scope": SCOPE_PRODUZIONE,
        "grantType": "client_credentials",
    }
    headers = {"POSTE_clientID": POSTE_CLIENT_ID, "Content-Type": "application/json"}
    resp = requests.post(AUTH_URL, json=payload, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    _token_cache["access_token"] = data["access_token"]
    _token_cache["expires_at"] = now + data.get("expires_in", 3599)
    return _token_cache["access_token"]


def _safe_description(ordine):
    titolo = ""
    try:
        titolo = (ordine.get("line_items") or [{}])[0].get("title", "")
    except Exception:
        titolo = ""
    testo = (titolo or "Merce varia").strip()
    testo = testo[:30]
    return testo or "Merce varia"


def _build_items(ordine, peso_grammi):
    line_items = ordine.get("line_items") or []
    if not line_items:
        return [{
            "itemNumber": "1",
            "taric": POSTE_DEFAULT_TARIC,
            "totalValue": "100",
            "quantity": "1",
            "totalWeight": str(max(1000, peso_grammi)),
            "originCountry": "IT",
        }]

    total_items = sum(max(1, int(item.get("quantity", 1))) for item in line_items)
    items = []
    for idx, item in enumerate(line_items, start=1):
        qty = max(1, int(item.get("quantity", 1)))
        grams = max(1, int(item.get("grams", 0) or 0))
        if grams <= 0:
            grams = max(1, peso_grammi // total_items)
        total_weight = grams * qty
        price = item.get("price")
        try:
            total_value = int(round(float(price or 0) * 100)) * qty
        except Exception:
            total_value = 100 * qty
        items.append({
            "itemNumber": str(idx),
            "taric": POSTE_DEFAULT_TARIC,
            "totalValue": str(max(1, total_value)),
            "quantity": str(qty),
            "totalWeight": str(max(1, total_weight)),
            "originCountry": "IT",
        })
    return items


def _extract_ldv(result):
    waybills = result.get("waybills") or []
    if waybills and isinstance(waybills, list):
        wb = waybills[0] or {}
        return wb.get("code") or wb.get("waybill") or wb.get("barcode") or ""
    return ""


def crea_spedizione_poste(ordine, paperless=False):
    try:
        token = get_poste_token()
        shipping = ordine.get("shipping_address") or {}
        paese = (shipping.get("country_code") or "IT").upper()
        peso_grammi = sum(max(1, int(item.get("grams", 500))) * max(1, int(item.get("quantity", 1))) for item in (ordine.get("line_items") or []))
        peso_grammi = max(1000, peso_grammi)
        country_code = "ITA1" if paese == "IT" else ISO2_TO_ISO4.get(paese, f"{paese}1")
        country_name = "Italia" if paese == "IT" else (shipping.get("country") or paese)
        product_code = "APT000901" if paese == "IT" else "APT001013"

        receiver = {
            "zipCode": shipping.get("zip", ""),
            "addressId": "",
            "streetNumber": "",
            "city": (shipping.get("city") or "").upper(),
            "address": shipping.get("address1", ""),
            "country": country_code,
            "countryName": country_name,
            "nameSurname": f"{shipping.get('first_name', '')} {shipping.get('last_name', '')}".strip() or ordine.get("email", "Cliente"),
            "contactName": f"{shipping.get('first_name', '')} {shipping.get('last_name', '')}".strip() or ordine.get("email", "Cliente"),
            "province": (shipping.get("province_code", "")[:2].upper() if paese == "IT" else ""),
            "email": ordine.get("email", ""),
            "phone": shipping.get("phone", ""),
            "cellphone": "",
            "note1": "",
            "note2": "",
        }

        data_block = {
            "declared": [{
                "weight": str(peso_grammi),
                "height": "10",
                "length": "30",
                "width": "25",
            }],
            "services": {},
            "sender": dict(MITTENTE),
            "receiver": receiver,
        }

        if paese == "IT":
            data_block["content"] = "Merce varia"
        else:
            data_block["description"] = _safe_description(ordine)
            data_block["packagingCode"] = POSTE_PACKAGING_CODE
            data_block["items"] = _build_items(ordine, peso_grammi)
            data_block["international"] = {
                "receiverType": "retailDelivery",
                "contentCode": POSTE_INTL_CONTENT_CODE,
            }

        payload = {
            "contractCode": POSTE_CONTRACT_CODE,
            "costCenterCode": POSTE_COST_CENTER,
            "paperless": paperless,
            "shipmentDate": time.strftime("%Y-%m-%dT%H:%M:%S.000+0000", time.gmtime()),
            "waybills": [{
                "clientReferenceId": str(ordine.get("order_number", ordine.get("id", "")))[:25],
                "printFormat": "A4",
                "product": product_code,
                "data": data_block,
            }],
        }

        if DEBUG_POSTE:
            print("DEBUG COUNTRY:", shipping.get("country"), paese)
            print("DEBUG MITTENTE PHONE:", data_block["sender"].get("phone"))
            print("PAYLOAD WAYBILL:", json.dumps(payload, ensure_ascii=False))

        headers = {
            "POSTE_clientID": POSTE_CLIENT_ID,
            "Authorization": token,
            "Content-Type": "application/json",
        }
        resp = requests.post(WAYBILL_URL, json=payload, headers=headers, timeout=20)
        resp.raise_for_status()
        result = resp.json()
        print(f"RISPOSTA POSTE: {result}")

        error_code = (result.get("result") or {}).get("errorCode")
        if error_code not in (0, "0", None):
            raise Exception((result.get("result") or {}).get("errorDescription", "Errore Poste sconosciuto"))

        ldv = _extract_ldv(result)
        if not ldv:
            raise Exception("Poste non ha restituito LDV")

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
                "statusDescription": "E",
                "customerType": "DQ",
            }
        }
        headers = {
            "POSTE_clientID": POSTE_CLIENT_ID,
            "Authorization": token,
            "Content-Type": "application/json",
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
            "Content-Type": "application/json",
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
                    "company": "Poste Italiane",
                    "number": ldv,
                    "url": f"https://www.poste.it/cerca/index.html#!/cerca/ricerca-spedizioni/{ldv}",
                },
                "notify_customer": True,
            }
        }
        resp = requests.post(url_f, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        print(f"Tracking aggiornato su Shopify per ordine #{order_number}: {ldv}")
        return True
    except Exception as e:
        print(f"Errore aggiornamento tracking Shopify: {e}")
        return False


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
            "max_delivery_date": None,
        }]
    }), 200


@app.route("/tracking/<ldv>", methods=["GET"])
def tracking(ldv):
    stato = get_tracking_poste(ldv)
    if stato:
        return jsonify({"ldv": ldv, "stato": stato}), 200
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
    shipping = ordine.get("shipping_address") or {}
    paese = (shipping.get("country_code") or "IT").upper()

    ordini_processati = carica_ordini()
    if ordine_id in ordini_processati:
        print(f"Ordine #{order_number} gia processato, ignoro duplicato")
        return jsonify({"status": "ok", "message": "already processed"}), 200

    if paese != "IT" and paese not in PAESE_ZONA:
        print(f"Ordine #{order_number} - paese {paese} non gestito, manuale")
        return jsonify({"status": "ok", "message": "paese non gestito"}), 200

    print(f"Ordine evaso: #{order_number} - {ordine.get('email', '')} - {paese}")
    ldv = crea_spedizione_poste(ordine, paperless=False)
    if not ldv:
        return jsonify({"status": "error", "message": "Errore creazione spedizione"}), 500

    ordini_processati.add(ordine_id)
    salva_ordini(ordini_processati)
    aggiorna_tracking_shopify(ordine_id, order_number, ldv)
    return jsonify({"status": "ok", "ldv": ldv}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
