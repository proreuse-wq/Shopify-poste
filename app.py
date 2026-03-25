from flask import Flask, request, jsonify
import requests
import time
import os
import json
import math
import re
from datetime import datetime, timezone

app = Flask(__name__)

# ─── CREDENZIALI POSTE ─────────────────────────────────────────────────────────
POSTE_CLIENT_ID = os.environ.get("POSTE_CLIENT_ID", "")
POSTE_SECRET_ID = os.environ.get("POSTE_SECRET_ID", "")
POSTE_COST_CENTER = "CDC-00080197"

def _normalize_phone(v):
    """Converte +39... in 0039... come richiesto dal manuale Poste per l'internazionale."""
    v = (v or "").strip()
    if v.startswith("+"):
        v = "00" + v[1:]
    return "".join(ch for ch in v if ch.isdigit())[:15]

MITTENTE_PHONE = _normalize_phone(os.environ.get("MITTENTE_PHONE", "+393775435992"))

def _mittente_phone_normalized():
    return MITTENTE_PHONE

MITTENTE = {
    "zipCode": "10070", "streetNumber": "30", "city": "VALLO TORINESE",
    "address": "Via Torino", "country": "ITA1", "countryName": "Italia",
    "nameSurname": "PROREUSE SRLS", "contactName": "PROREUSE SRLS",
    "province": "TO", "email": "proreuse1622@gmail.com",
    "phone": MITTENTE_PHONE, "cellphone": MITTENTE_PHONE, "note1": "", "note2": ""
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

def clean_phone(value):
    """Normalizza il telefono al formato 00<prefisso><numero> richiesto da Poste
    per spedizioni internazionali (es. +393771234567 → 00393771234567).
    """
    v = (value or "").strip()
    # Sostituisce il + iniziale con 00
    if v.startswith("+"):
        v = "00" + v[1:]
    digits = "".join(ch for ch in v if ch.isdigit())
    result = digits[:15]
    # Fallback al telefono mittente (già normalizzato a 00...)
    return result or _mittente_phone_normalized()


def sanitize(value, n):
    """Rimuove caratteri non-ASCII e tronca a n caratteri.
    Poste rifiuta caratteri speciali come ß, é, ü nei campi stringa.
    """
    import unicodedata
    v = unicodedata.normalize("NFKD", (value or ""))
    v = v.encode("ascii", "ignore").decode("ascii")
    return v[:n].strip()


def trunc(value, n):
    return (value or "")[:n]


def split_address(address1, address2=""):
    raw = f"{address1 or ''} {address2 or ''}".strip()
    raw = re.sub(r"\s+", " ", raw)
    # Sanitizza prima di estrarre numero civico
    raw = sanitize(raw, 200)
    m = re.search(r"\b(\d+[A-Za-z]?)\b", raw)
    street_number = m.group(1)[:4] if m else ""
    street = raw.replace(m.group(0), "", 1).strip(" ,") if m else raw
    return trunc(street, 40), trunc(street_number, 4)


def iso4_from_iso2(country_code):
    cc = (country_code or "").upper().strip()
    return ISO2_TO_ISO4.get(cc, "")


def log_poste_response(prefix, response_json):
    print(f"{prefix}: {json.dumps(response_json, ensure_ascii=False)}")


# ─── MAPPA PAESI → ZONA ────────────────────────────────────────────────────────
PAESE_ZONA = {
    "DE": 1, "NL": 1, "PL": 1, "EE": 1, "LV": 1, "LT": 1, "LI": 1,
    "AT": 2, "BE": 2, "DK": 2, "FR": 2, "FI": 2, "LU": 2, "SE": 2,
    "PT": 2, "CZ": 2, "RO": 2, "SK": 2, "SI": 2, "ES": 2, "HU": 2, "MC": 2,
    "BG": 3, "HR": 3, "GR": 3, "MT": 3, "NO": 3, "CH": 3,
    "CY": 4, "IE": 4,
    "GB": 10,
}

# ─── MAPPA ISO2 → ISO4 ─────────────────────────────────────────────────────────
ISO2_TO_ISO4 = {
    "AT": "AUT1", "BE": "BEL1", "BG": "BGR1", "HR": "HRV1",
    "CY": "CYP1", "CZ": "CZE1", "DK": "DNK1", "EE": "EST1",
    "FI": "FIN1", "FR": "FRA1", "DE": "DEU1", "GR": "GRC1",
    "HU": "HUN1", "IE": "IRL1", "LV": "LVA1", "LT": "LTU1",
    "LU": "LUX1", "MT": "MLT1", "NL": "NLD1", "PL": "POL1",
    "PT": "PRT1", "RO": "ROU1", "SK": "SVK1", "SI": "SVN1",
    "ES": "ESP1", "SE": "SWE1", "GB": "GBR1", "NO": "NOR1",
    "CH": "CHE1", "LI": "LIE1", "MC": "MCO1",
}

# ─── TARIFFE INTERNAZIONALI (centesimi) ────────────────────────────────────────
TARIFFE_KG = {
    1: [910,1120,1150,1240,1260,1362,1426,1472,1518,1638,
        1739,1850,1980,2100,2270,2320,2450,2530,2590,2650,
        2690,2780,2900,3080,3150,3320,3450,3550,3620,3700],
    2: [993,1119,1129,1334,1355,1549,1570,1603,1667,1775,
        2098,2205,2313,2420,2528,2829,2904,2947,2990,3012,
        3227,3335,3388,3442,3496,3603,3716,3786,3856,3926],
    3: [1555,1665,1750,1885,2055,2210,2310,2415,2515,2715,
        3010,3125,3235,3350,3465,3575,3690,3805,3915,4030,
        4145,4255,4370,4485,4595,4710,4825,4935,5050,5165],
    4: [1737,1901,2065,2469,2501,2633,2829,3048,3157,3387,
        3627,3703,3769,3867,3933,3955,4053,4151,4250,4348,
        4446,4544,4643,4741,4839,4938,5036,5134,5233,5331],
    10: [1440,1716,1812,1932,2028,2064,2136,2256,2340,2406,
         2592,2664,2730,2802,2874,2940,3012,3060,3150,3252,
         3529,3661,3841,3937,3961,3985,4201,4273,4381,4501],
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
    base30 = TARIFFE_KG[zona][29]
    return base30 + 470 * TARIFFA_PER_KG[zona]


def calcola_prezzo_italia(peso_kg):
    if peso_kg <= 2:   return 430
    if peso_kg <= 5:   return 500
    if peso_kg <= 10:  return 600
    if peso_kg <= 20:  return 700
    if peso_kg <= 30:  return 830
    if peso_kg <= 50:  return 1460
    if peso_kg <= 70:  return 1600
    if peso_kg <= 100: return 1940
    if peso_kg <= 200: return 3880
    if peso_kg <= 300: return 5820
    if peso_kg <= 400: return 7760
    if peso_kg <= 500: return 9700
    return 9700


def stima_dimensioni(peso_kg):
    """Stima dimensioni realistiche del collo in base al peso (valori in cm interi).
    Restituisce (height, length, width) come stringhe.
    """
    if peso_kg <= 1:
        return "10", "20", "15"
    elif peso_kg <= 5:
        return "15", "30", "20"
    elif peso_kg <= 10:
        return "20", "40", "30"
    elif peso_kg <= 20:
        return "25", "50", "35"
    else:
        return "30", "60", "40"


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


def get_num_colli(ordine):
    """Legge il tag 'colli:N' dall'ordine Shopify. Default 1."""
    tags = ordine.get("tags", "") or ""
    for tag in tags.split(","):
        tag = tag.strip().lower()
        if tag.startswith("colli:"):
            try:
                n = int(tag.split(":")[1])
                return max(1, min(n, 10))  # limite 1-10 colli
            except ValueError:
                pass
    return 1


def build_declared(num_colli, peso_grammi_totale, h, l, w, extra_fields=None):
    """Costruisce la lista declared[] distribuendo il peso equamente sui colli."""
    peso_per_collo = max(1, peso_grammi_totale // num_colli)
    collo = {"weight": peso_per_collo, "height": h, "length": l, "width": w}
    if extra_fields:
        collo.update(extra_fields)
    return [collo] * num_colli


def crea_spedizione_italia(ordine, token, paperless=False):
    shipping = ordine.get("shipping_address", {})
    peso_grammi = sum(
        item.get("grams", 500) * item.get("quantity", 1)
        for item in ordine.get("line_items", [])
    )
    peso_kg = max(1, round(peso_grammi / 1000))
    num_colli = get_num_colli(ordine)
    h, l, w = stima_dimensioni(peso_kg / num_colli)
    services = {"APT000945": {}} if num_colli > 1 else {}

    payload = {
        "costCenterCode": POSTE_COST_CENTER,
        "paperless": paperless,
        "shipmentDate": time.strftime("%Y-%m-%dT%H:%M:%S.000+0000", time.gmtime()),
        "waybills": [{
            "clientReferenceId": str(ordine.get("order_number", ordine.get("id", "")))[:25],
            "printFormat": "A4",
            "product": "APT000901",
            "data": {
                "declared": build_declared(num_colli, peso_grammi, h, l, w),
                "content": "Merce varia",
                "services": services,
                "sender": MITTENTE,
                "receiver": {
                    "zipCode": shipping.get("zip", ""),
                    "addressId": "", "streetNumber": "",
                    "city": shipping.get("city", "").upper(),
                    "address": shipping.get("address1", ""),
                    "country": "ITA1", "countryName": "Italia",
                    "nameSurname": f"{shipping.get('first_name', '')} {shipping.get('last_name', '')}".strip(),
                    "contactName": f"{shipping.get('first_name', '')} {shipping.get('last_name', '')}".strip(),
                    "province": shipping.get("province_code", "")[:2].upper(),
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
    return resp.json()


def crea_spedizione_internazionale(ordine, token, paperless=False):
    shipping = ordine.get("shipping_address", {}) or {}
    billing = ordine.get("billing_address", {}) or {}

    paese_iso2 = (shipping.get("country_code") or billing.get("country_code") or "").upper()
    country_code = iso4_from_iso2(paese_iso2)
    if not country_code:
        raise RuntimeError(f"Paese non supportato da mappa ISO4: {paese_iso2}")

    country_name = shipping.get("country") or billing.get("country") or paese_iso2
    city = sanitize((shipping.get("city") or billing.get("city") or "").upper(), 30)
    province = trunc((shipping.get("province_code") or billing.get("province_code") or "").upper(), 2)
    zip_code = trunc(shipping.get("zip") or billing.get("zip") or "", 7)

    # Per l'internazionale mettiamo tutto in address e streetNumber vuoto
    # (alcuni paesi non accettano streetNumber separato)
    raw_address = f"{shipping.get('address1') or billing.get('address1') or ''} {shipping.get('address2') or billing.get('address2') or ''}".strip()
    street = sanitize(raw_address, 40)
    street_number = ""

    first_name = shipping.get("first_name") or billing.get("first_name") or ""
    last_name = shipping.get("last_name") or billing.get("last_name") or ""
    company = shipping.get("company") or billing.get("company") or ""
    full_name = (f"{first_name} {last_name}").strip() or company or "CLIENTE"
    name_surname = sanitize(company or full_name, 35)
    contact_name = sanitize(full_name, 35)

    phone = clean_phone(shipping.get("phone") or billing.get("phone") or ordine.get("phone") or "")
    email = trunc(ordine.get("email") or "cliente@example.com", 50)

    line_items = ordine.get("line_items", []) or []
    description = trunc(str((line_items[0] if line_items else {}).get("title", "Merce varia")), 30)

    total_weight = 0
    items = []
    for idx, item in enumerate(line_items, start=1):
        qty = max(1, int(item.get("quantity", 1) or 1))
        grams_each = max(1, int(item.get("grams", 500) or 500))
        total_item_weight = grams_each * qty
        total_weight += total_item_weight
        try:
            unit_price = float(str(item.get("price", "0")).replace(",", "."))
            total_value = int(round(unit_price * qty * 100))
        except Exception:
            total_value = 100

        items.append({
            "itemNumber": str(idx)[:1],
            "description": trunc(str(item.get("title", "Articolo")), 30),
            "quantity": str(qty),
            "totalValue": str(max(1, total_value)),
            "totalWeight": str(max(1, total_item_weight)),
            # PAESE_ORIGINE: codice ISO2 del paese di produzione - default IT (Italia)
            "originCountry": "IT",
            # TARIC: codice internazionale merci - "39269090" = articoli vari in plastica
            # (codice generico accettato da Poste; aggiornare per categorie specifiche)
            "taric": "39269090",
        })

    total_weight = max(1, total_weight)
    total_weight_kg = total_weight / 1000
    num_colli = get_num_colli(ordine)
    h, l, w = stima_dimensioni(total_weight_kg / num_colli)
    receiver_type = "businessDelivery" if company else "retailDelivery"

    def build_payload(receiver_type_value):
        services = {"APT000945": {}} if num_colli > 1 else {}
        return {
            "costCenterCode": POSTE_COST_CENTER,
            "paperless": paperless,
            "shipmentDate": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+0000"),
            "waybills": [{
                "clientReferenceId": str(ordine.get("order_number", ordine.get("id", "")))[:25],
                "printFormat": "A4",
                "product": "APT001013",
                "data": {
                    "declared": build_declared(num_colli, total_weight, h, l, w,
                                               {"packagingCode": "C", "description": description}),
                    "description": description,
                    "services": services,
                    "items": items,
                    "international": {
                        # receiverType: "retailDelivery" (privato) o "businessDelivery" (azienda)
                        # contentCode: "2" = merce commerciale (valore generico per APT001013)
                        "receiverType": receiver_type_value,
                        "contentCode": "2"
                    },
                    "sender": MITTENTE,
                    "receiver": {
                        "zipCode": zip_code,
                        "addressId": "",
                        "streetNumber": street_number,
                        "city": city,
                        "address": street,
                        "country": country_code,
                        "countryName": sanitize(country_name, 30),
                        "nameSurname": name_surname,
                        "contactName": contact_name,
                        "province": province,
                        "email": email,
                        "phone": phone,
                        "cellphone": phone,
                        "note1": "",
                        "note2": "",
                    }
                }
            }]
        }

    headers = {
        "POSTE_clientID": POSTE_CLIENT_ID,
        "Authorization": token,
        "Content-Type": "application/json"
    }

    attempts = [receiver_type]
    alt = "retailDelivery" if receiver_type == "businessDelivery" else "businessDelivery"
    if alt not in attempts:
        attempts.append(alt)

    last_json = None
    for idx, candidate in enumerate(attempts, start=1):
        payload = build_payload(candidate)
        print(f"PAYLOAD INTERNAZIONALE TENTATIVO {idx}: {json.dumps(payload, ensure_ascii=False)}")
        resp = requests.post(WAYBILL_URL, json=payload, headers=headers, timeout=20)
        resp.raise_for_status()
        response_json = resp.json()
        log_poste_response(f"RISPOSTA POSTE INTL TENTATIVO {idx}", response_json)
        last_json = response_json
        error_desc = str(response_json.get("result", {}).get("errorDescription", ""))
        error_code = response_json.get("result", {}).get("errorCode")
        if error_code in (0, "0", None):
            return response_json
        if "carriers non compatibili" not in error_desc.lower():
            return response_json

    return last_json or {"result": {"errorCode": 999, "errorDescription": "Errore internazionale sconosciuto"}}


def crea_spedizione_poste(ordine, paperless=False):
    try:
        token = get_poste_token()
        shipping = ordine.get("shipping_address", {}) or {}
        paese = shipping.get("country_code", "IT").upper()

        if paese == "IT":
            result = crea_spedizione_italia(ordine, token, paperless)
        else:
            result = crea_spedizione_internazionale(ordine, token, paperless)

        print(f"RISPOSTA POSTE: {result}")

        error_code = result.get("result", {}).get("errorCode")
        if error_code not in (0, "0", None):
            raise RuntimeError(result.get("result", {}).get("errorDescription", f"Errore {error_code}"))

        ldv = result.get("waybills", [{}])[0].get("code", "")
        if not ldv:
            raise RuntimeError("LDV non restituita")

        print(f"Spedizione creata! LDV: {ldv} - Paese: {paese}")
        return ldv
    except Exception as e:
        print(f"Errore creazione spedizione Poste: {e}")
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
            print(f"Nessun fulfillment_order per ordine #{order_number}")
            return False
        line_items_by_fulfillment = [
            {"fulfillment_order_id": fo["id"]}
            for fo in fulfillment_orders
            if fo.get("status") in ("open", "in_progress")
        ]
        if not line_items_by_fulfillment:
            # Ordine già fulfilled — aggiorna solo il tracking sull'existing fulfillment
            print(f"Ordine #{order_number} già evaso, tento aggiornamento tracking diretto")
            existing = [fo for fo in fulfillment_orders if fo.get("status") == "closed"]
            if existing:
                fulfillment_id = existing[0].get("assigned_fulfillment_order_id") or existing[0].get("id")
                url_upd = f"https://{SHOPIFY_SHOP}/admin/api/{SHOPIFY_API_VERSION}/fulfillments/{fulfillment_id}/update_tracking.json"
                upd_payload = {"fulfillment": {"notify_customer": False, "tracking_info": {
                    "company": "Poste Italiane", "number": ldv,
                    "url": f"https://www.poste.it/cerca/index.html#!/cerca/ricerca-spedizioni/{ldv}"
                }}}
                resp_upd = requests.post(url_upd, json=upd_payload, headers=headers, timeout=10)
                print(f"Update tracking esistente: {resp_upd.status_code} {resp_upd.text[:200]}")
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
        if not resp.ok:
            print(f"Errore Shopify {resp.status_code}: {resp.text[:300]}")
            return False
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
    data = request.get_json(silent=True) or {}
    peso_grammi = 0
    paese = "IT"
    if "rate" in data:
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
            "max_delivery_date": None
        }]
    }), 200


@app.route("/tracking/<ldv>", methods=["GET"])
def tracking(ldv):
    stato = None
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
            stato = tracking_events[-1].get("statusDescription", "")
    except Exception as e:
        print(f"Errore tracking: {e}")

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

    shipping = ordine.get("shipping_address", {}) or {}
    paese = shipping.get("country_code", "IT").upper()

    # Gestisce solo Italia e paesi nella lista Poste
    if paese != "IT" and paese not in PAESE_ZONA:
        print(f"Ordine #{order_number} - paese {paese} non gestito, manuale")
        return jsonify({"status": "ok", "message": "paese non gestito"}), 200

    ordini_processati = carica_ordini()
    if ordine_id in ordini_processati:
        print(f"Ordine #{order_number} gia processato, ignoro duplicato")
        return jsonify({"status": "ok", "message": "already processed"}), 200

    print(f"Ordine evaso: #{order_number} - {ordine.get('email', '')} - {paese}")

    ldv = crea_spedizione_poste(ordine, paperless=False)

    if ldv:
        ordini_processati.add(ordine_id)
        salva_ordini(ordini_processati)
        aggiorna_tracking_shopify(ordine_id, order_number, ldv)
        return jsonify({"status": "ok", "ldv": ldv}), 200
    else:
        return jsonify({"status": "error", "message": "Errore creazione spedizione"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
