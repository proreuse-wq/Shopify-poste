from flask import Flask, request, jsonify
import requests
import time
import os
import json
import math
from typing import Any, Dict, List, Optional

app = Flask(__name__)

# ─── CREDENZIALI POSTE ─────────────────────────────────────────────────────────
POSTE_CLIENT_ID = os.environ.get("POSTE_CLIENT_ID", "")
POSTE_SECRET_ID = os.environ.get("POSTE_SECRET_ID", "")
POSTE_COST_CENTER = os.environ.get("POSTE_COST_CENTER", "CDC-00080197")
POSTE_CONTRACT_CODE = os.environ.get("POSTE_CONTRACT_CODE", "")

MITTENTE = {
    "zipCode": os.environ.get("MITTENTE_ZIP", "10070"),
    "streetNumber": os.environ.get("MITTENTE_STREET_NUMBER", "30"),
    "city": os.environ.get("MITTENTE_CITY", "VALLO TORINESE"),
    "address": os.environ.get("MITTENTE_ADDRESS", "Via Torino"),
    "country": "ITA1",
    "countryName": "Italia",
    "nameSurname": os.environ.get("MITTENTE_NAME", "PROREUSE SRLS"),
    "contactName": os.environ.get("MITTENTE_CONTACT", "PROREUSE SRLS"),
    "province": os.environ.get("MITTENTE_PROVINCE", "TO"),
    "email": os.environ.get("MITTENTE_EMAIL", "proreuse1622@gmail.com"),
    "phone": os.environ.get("MITTENTE_PHONE") or os.environ.get("POSTE_SENDER_PHONE") or "+393711998531",
    "cellphone": os.environ.get("MITTENTE_CELLPHONE", ""),
    "note1": "",
    "note2": "",
}

AUTH_URL = "https://apiw.gp.posteitaliane.it/gp/internet/user/sessions"
WAYBILL_URL = "https://apiw.gp.posteitaliane.it/gp/internet/postalandlogistics/parcel/waybill"
TRACKING_URL = "https://apiw.gp.posteitaliane.it/gp/internet/postalandlogistics/parcel/tracking"
INTERNATIONAL_NATIONS_URL = "https://apiw.gp.posteitaliane.it/gp/internet/postalandlogistics/parcel/international/nations"
INTERNATIONAL_NATION_DETAILS_URL = "https://apiw.gp.posteitaliane.it/gp/internet/postalandlogistics/parcel/international/nation/details"
WAYBILL_SERVICES_URL = "https://apiw.gp.posteitaliane.it/gp/internet/postalandlogistics/parcel/waybill/services"
SCOPE_PRODUZIONE = "https://postemarketplace.onmicrosoft.com/d6a78063-5570-4a87-bbd7-07326e6855d1/.default"

# ─── CREDENZIALI SHOPIFY ───────────────────────────────────────────────────────
SHOPIFY_TOKEN = os.environ.get("SHOPIFY_TOKEN", "")
SHOPIFY_SHOP = os.environ.get("SHOPIFY_SHOP", "")
SHOPIFY_API_VERSION = os.environ.get("SHOPIFY_API_VERSION", "2026-01")

# ─── OPZIONI CONFIGURABILI ─────────────────────────────────────────────────────
DEFAULT_TARIC = os.environ.get("POSTE_DEFAULT_TARIC", "0000000000")
DEFAULT_PACKAGING_CODE = os.environ.get("POSTE_PACKAGING_CODE", "C")
DEFAULT_INTL_CONTENT_DESCRIPTION = os.environ.get("POSTE_INTL_DESCRIPTION", "Merce")
DEFAULT_RECEIVER_TYPE = os.environ.get("POSTE_DEFAULT_RECEIVER_TYPE", "retailDelivery")
DEFAULT_ORIGIN_COUNTRY_ISO2 = os.environ.get("POSTE_ORIGIN_COUNTRY_ISO2", "IT")
ENABLE_PAPERLESS = os.environ.get("POSTE_PAPERLESS", "false").lower() == "true"
DEBUG_POSTE = os.environ.get("DEBUG_POSTE", "false").lower() == "true"

# ─── CACHE TOKEN / DATI INTERNAZIONALI ─────────────────────────────────────────
_token_cache = {"access_token": None, "expires_at": 0}
_nations_cache = {"data": None, "expires_at": 0}
_country_details_cache: Dict[str, Dict[str, Any]] = {}

ORDINI_FILE = "/tmp/ordini_processati.json"

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
    "HU": "HUN1", "IE": "IRL1", "IT": "ITA1", "LV": "LVA1",
    "LT": "LTU1", "LU": "LUX1", "MT": "MLT1", "NL": "NLD1",
    "PL": "POL1", "PT": "PRT1", "RO": "ROU1", "SK": "SVK1",
    "SI": "SVN1", "ES": "ESP1", "SE": "SWE1", "GB": "GBR1",
    "NO": "NOR1", "CH": "CHE1", "LI": "LIE1", "MC": "MCO1",
}

# ─── TARIFFE INTERNAZIONALI HD (centesimi) ─────────────────────────────────────
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


# ─── UTILS ─────────────────────────────────────────────────────────────────────
def log_debug(*args: Any) -> None:
    if DEBUG_POSTE:
        print(*args)


def carica_ordini() -> set:
    try:
        with open(ORDINI_FILE, "r") as f:
            return set(json.load(f))
    except Exception:
        return set()


def salva_ordini(ordini: set) -> None:
    try:
        with open(ORDINI_FILE, "w") as f:
            json.dump(list(ordini), f)
    except Exception:
        pass


def is_blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def clean_text(value: Any, max_len: Optional[int] = None, upper: bool = False) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.replace("\n", " ").replace("\r", " ").split())
    if upper:
        text = text.upper()
    if max_len:
        text = text[:max_len]
    return text


def digits_only(value: Any, plus_allowed: bool = False, max_len: Optional[int] = None) -> str:
    if value is None:
        return ""
    value = str(value).strip()
    if plus_allowed and value.startswith("+"):
        cleaned = "+" + "".join(ch for ch in value[1:] if ch.isdigit())
    else:
        cleaned = "".join(ch for ch in value if ch.isdigit())
    if max_len:
        cleaned = cleaned[:max_len]
    return cleaned


def to_int_string(value: Any, minimum: int = 0) -> str:
    try:
        ivalue = int(round(float(value)))
    except Exception:
        ivalue = minimum
    return str(max(minimum, ivalue))


def euro_to_cents_string(value: Any, fallback_cents: int = 100) -> str:
    try:
        if isinstance(value, str):
            value = value.replace(",", ".")
        cents = int(round(float(value) * 100))
        if cents > 0:
            return str(cents)
    except Exception:
        pass
    return str(fallback_cents)


def round_up_kg_from_grams(grams: int) -> int:
    grams = max(1, int(grams))
    return max(1, math.ceil(grams / 1000))


def get_order_weight_grams(ordine: Dict[str, Any]) -> int:
    total = 0
    for item in ordine.get("line_items", []):
        grams = item.get("grams")
        quantity = int(item.get("quantity", 1) or 1)
        if grams is None:
            grams = 500
        total += int(grams) * quantity
    return max(total, 1)


def get_order_total_cents(ordine: Dict[str, Any]) -> int:
    total_price = ordine.get("total_price")
    try:
        return max(1, int(round(float(str(total_price).replace(",", ".")) * 100)))
    except Exception:
        pass

    total = 0
    for item in ordine.get("line_items", []):
        quantity = int(item.get("quantity", 1) or 1)
        price = item.get("price") or item.get("price_set", {}).get("shop_money", {}).get("amount")
        try:
            total += int(round(float(str(price).replace(",", ".")) * 100)) * quantity
        except Exception:
            pass
    return max(total, 100)


def infer_receiver_type(ordine: Dict[str, Any], peso_kg: int) -> str:
    shipping = ordine.get("shipping_address", {}) or {}
    company = clean_text(shipping.get("company"), max_len=35)
    if company:
        return "businessDelivery"
    if peso_kg > 30:
        return "businessDelivery"
    return DEFAULT_RECEIVER_TYPE if DEFAULT_RECEIVER_TYPE in {"retailDelivery", "businessDelivery"} else "retailDelivery"


def get_item_taric(item: Dict[str, Any]) -> str:
    candidates = [
        item.get("harmonized_system_code"),
        item.get("hs_code"),
        item.get("taric"),
        item.get("sku"),
    ]
    for candidate in candidates:
        digits = "".join(ch for ch in str(candidate or "") if ch.isdigit())
        if len(digits) >= 6:
            return digits[:10].ljust(10, "0")
    return DEFAULT_TARIC


def allocate_item_weight(total_weight: int, item: Dict[str, Any], order_line_items: List[Dict[str, Any]]) -> int:
    explicit_weight = item.get("grams")
    quantity = int(item.get("quantity", 1) or 1)
    if explicit_weight not in (None, ""):
        return max(1, int(explicit_weight) * quantity)

    total_qty = sum(int(li.get("quantity", 1) or 1) for li in order_line_items) or 1
    return max(1, round(total_weight / total_qty) * quantity)


def build_poste_items(ordine: Dict[str, Any], total_weight_grams: int) -> List[Dict[str, str]]:
    line_items = ordine.get("line_items", []) or []
    order_total_cents = get_order_total_cents(ordine)
    raw_items: List[Dict[str, str]] = []
    computed_total = 0

    for idx, item in enumerate(line_items, start=1):
        quantity = max(1, int(item.get("quantity", 1) or 1))
        unit_price = item.get("price") or item.get("price_set", {}).get("shop_money", {}).get("amount")
        total_value = None
        try:
            total_value = int(round(float(str(unit_price).replace(",", ".")) * 100)) * quantity
        except Exception:
            total_value = None

        if total_value is None or total_value <= 0:
            total_value = max(1, round(order_total_cents / max(1, len(line_items))))

        item_weight = allocate_item_weight(total_weight_grams, item, line_items)
        computed_total += total_value

        raw_items.append({
            "itemNumber": str(idx),
            "description": clean_text(item.get("title") or item.get("name") or DEFAULT_INTL_CONTENT_DESCRIPTION, max_len=30),
            "taric": get_item_taric(item),
            "totalValue": str(max(1, total_value)),
            "quantity": str(quantity),
            "totalWeight": str(max(1, item_weight)),
            "originCountry": DEFAULT_ORIGIN_COUNTRY_ISO2,
        })

    if raw_items and computed_total != order_total_cents:
        delta = order_total_cents - computed_total
        last_value = int(raw_items[-1]["totalValue"])
        raw_items[-1]["totalValue"] = str(max(1, last_value + delta))

    return raw_items


def calcola_prezzo_internazionale(zona: int, peso_kg: float) -> Optional[int]:
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
    per_kg = TARIFFA_PER_KG[zona]
    return base30 + 470 * per_kg


def calcola_prezzo_italia(peso_kg: float) -> int:
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
    if peso_kg <= 500:
        return 9700
    return 9700


# ─── FUNZIONI POSTE ────────────────────────────────────────────────────────────
def get_poste_token() -> str:
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


def poste_headers() -> Dict[str, str]:
    return {
        "POSTE_clientID": POSTE_CLIENT_ID,
        "Authorization": get_poste_token(),
        "Content-Type": "application/json",
    }


def get_international_nations(force_refresh: bool = False) -> List[Dict[str, Any]]:
    now = time.time()
    if not force_refresh and _nations_cache["data"] and now < _nations_cache["expires_at"]:
        return _nations_cache["data"]

    resp = requests.post(INTERNATIONAL_NATIONS_URL, json={}, headers=poste_headers(), timeout=20)
    resp.raise_for_status()
    data = resp.json()
    countries = data.get("countries", []) or []
    _nations_cache["data"] = countries
    _nations_cache["expires_at"] = now + 6 * 3600
    return countries


def get_country_product_details(country_code_iso4: str, product_code: str) -> Dict[str, Any]:
    cache_key = f"{country_code_iso4}:{product_code}"
    cached = _country_details_cache.get(cache_key)
    if cached and time.time() < cached["expires_at"]:
        return cached["data"]

    payload = {"countryCode": country_code_iso4, "productCode": product_code}
    resp = requests.post(INTERNATIONAL_NATION_DETAILS_URL, json=payload, headers=poste_headers(), timeout=20)
    resp.raise_for_status()
    data = resp.json()
    _country_details_cache[cache_key] = {"data": data, "expires_at": time.time() + 6 * 3600}
    return data


def get_waybill_services(product_code: str, sender: Dict[str, Any], receiver: Dict[str, Any], declared: List[Dict[str, str]], receiver_type: str, content_code: Optional[str] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "costCenterCode": POSTE_COST_CENTER,
        "product": product_code,
        "sender": sender,
        "receiver": receiver,
        "declared": declared,
        "cashAmount": "",
        "cashType": "",
        "international": {"receiverType": receiver_type},
    }
    if POSTE_CONTRACT_CODE:
        payload["contractCode"] = POSTE_CONTRACT_CODE
    if content_code:
        payload["international"]["contentCode"] = content_code

    resp = requests.post(WAYBILL_SERVICES_URL, json=payload, headers=poste_headers(), timeout=20)
    resp.raise_for_status()
    return resp.json()


def get_country_info_by_iso2(iso2: str) -> Optional[Dict[str, Any]]:
    iso2 = (iso2 or "").upper()
    nations = get_international_nations()
    for country in nations:
        if country.get("iso2", "").upper() == iso2:
            return country
    return None


def choose_content_code(country_details: Dict[str, Any]) -> str:
    forced = clean_text(os.environ.get("POSTE_INTL_CONTENT_CODE"), max_len=3)
    if forced:
        return forced
    content_list = country_details.get("content", []) or []
    for row in content_list:
        allowed = str(row.get("content_allowed", "")).lower()
        if row.get("content_allowed") in (True, 1, "1") or allowed in {"true", "yes", "y", "s"}:
            code = clean_text(row.get("content_code"), max_len=3)
            if code:
                return code
    for row in content_list:
        code = clean_text(row.get("content_code"), max_len=3)
        if code:
            return code
    return "999"


def build_receiver(ordine: Dict[str, Any], country_code: str, country_name: str, paese: str) -> Dict[str, str]:
    shipping = ordine.get("shipping_address", {}) or {}
    full_name = clean_text(f"{shipping.get('first_name', '')} {shipping.get('last_name', '')}", max_len=35)
    company = clean_text(shipping.get("company"), max_len=35)
    contact_name = full_name or company or "CLIENTE"
    name_surname = company or full_name or "CLIENTE"

    address1 = clean_text(shipping.get("address1"), max_len=40)
    address2 = clean_text(shipping.get("address2"), max_len=40)
    full_address = clean_text(" ".join([part for part in [address1, address2] if part]), max_len=40)

    phone = digits_only(shipping.get("phone") or ordine.get("phone") or shipping.get("company"), plus_allowed=True, max_len=15)
    if not phone:
        phone = digits_only(MITTENTE.get("phone"), plus_allowed=True, max_len=15)

    email = clean_text(ordine.get("email") or shipping.get("email") or MITTENTE.get("email"), max_len=50)

    receiver = {
        "zipCode": clean_text(shipping.get("zip"), max_len=7),
        "addressId": "",
        "streetNumber": clean_text(shipping.get("address2"), max_len=4),
        "city": clean_text(shipping.get("city"), max_len=30, upper=True),
        "address": full_address,
        "country": country_code,
        "countryName": clean_text(country_name or shipping.get("country") or paese, max_len=30),
        "nameSurname": name_surname,
        "contactName": contact_name,
        "province": clean_text(shipping.get("province_code"), max_len=2, upper=True) if paese == "IT" else clean_text(shipping.get("province_code"), max_len=2, upper=True),
        "email": email,
        "phone": phone,
        "cellphone": "",
        "note1": "",
        "note2": "",
    }

    return receiver


def validate_required_env() -> None:
    missing = []
    for key, value in {
        "POSTE_CLIENT_ID": POSTE_CLIENT_ID,
        "POSTE_SECRET_ID": POSTE_SECRET_ID,
        "SHOPIFY_TOKEN": SHOPIFY_TOKEN,
        "SHOPIFY_SHOP": SHOPIFY_SHOP,
    }.items():
        if is_blank(value):
            missing.append(key)
    if missing:
        raise RuntimeError(f"Variabili mancanti: {', '.join(missing)}")


def build_poste_payload(ordine: Dict[str, Any], paperless: bool = False) -> Dict[str, Any]:
    shipping = ordine.get("shipping_address", {}) or {}
    paese = clean_text(shipping.get("country_code") or "IT", max_len=2, upper=True)
    log_debug("DEBUG COUNTRY:", shipping.get("country"), paese)
    log_debug("DEBUG MITTENTE PHONE:", MITTENTE.get("phone"))
    peso_grammi = get_order_weight_grams(ordine)
    peso_kg = round_up_kg_from_grams(peso_grammi)

    if paese == "IT":
        product_code = "APT000901"
        country_code = "ITA1"
        country_name = "Italia"
    else:
        product_code = "APT001013"
        country_info = get_country_info_by_iso2(paese)
        if not country_info:
            raise ValueError(f"Paese {paese} non restituito da international/nations")
        if not country_info.get("active"):
            raise ValueError(f"Paese {paese} non attivo su Poste")
        if product_code not in (country_info.get("products") or []):
            raise ValueError(f"Prodotto {product_code} non disponibile per il paese {paese}")
        country_code = clean_text(country_info.get("iso4") or ISO2_TO_ISO4.get(paese, f"{paese}1"), max_len=4, upper=True)
        country_name = clean_text(country_info.get("name") or shipping.get("country") or paese, max_len=30)

    receiver = build_receiver(ordine, country_code, country_name, paese)

    declared: List[Dict[str, str]] = [{
        "weight": str(peso_grammi),
        "height": "10",
        "length": "30",
        "width": "25",
    }]

    data_block: Dict[str, Any] = {
        "declared": declared,
        "sender": MITTENTE,
        "receiver": receiver,
    }

    # Poste rejects empty services on some intl products; omit when empty.
    if isinstance(data_block.get("services"), dict) and not data_block["services"]:
        data_block.pop("services", None)

    if paese == "IT":
        data_block["content"] = clean_text(DEFAULT_INTL_CONTENT_DESCRIPTION, max_len=30)
    else:
        country_details = get_country_product_details(country_code, product_code)
        content_code = choose_content_code(country_details)
        receiver_type = infer_receiver_type(ordine, peso_kg)

        first_title = ""
        try:
            first_title = clean_text(((ordine.get("line_items") or [{}])[0]).get("title"), max_len=30)
        except Exception:
            first_title = ""
        data_block["description"] = first_title or clean_text(DEFAULT_INTL_CONTENT_DESCRIPTION, max_len=30) or "Merce"
        data_block["packagingCode"] = DEFAULT_PACKAGING_CODE
        data_block["items"] = build_poste_items(ordine, peso_grammi)
        data_block["international"] = {
            "receiverType": receiver_type,
            "contentCode": content_code,
        }

        try:
            service_info = get_waybill_services(
                product_code=product_code,
                sender=MITTENTE,
                receiver=receiver,
                declared=declared,
                receiver_type=receiver_type,
                content_code=content_code,
            )
            log_debug("WAYBILL SERVICES:", json.dumps(service_info, ensure_ascii=False))
        except Exception as exc:
            log_debug(f"waybill/services non disponibile: {exc}")

    payload = {
        "costCenterCode": POSTE_COST_CENTER,
        "paperless": bool(paperless),
        "shipmentDate": time.strftime("%Y-%m-%dT%H:%M:%S.000+0000", time.gmtime()),
        "waybills": [{
            "clientReferenceId": str(ordine.get("order_number", ordine.get("id", "")))[:25],
            "printFormat": "A4",
            "product": product_code,
            "data": data_block,
        }],
    }
    if POSTE_CONTRACT_CODE:
        payload["contractCode"] = POSTE_CONTRACT_CODE
    return payload


def crea_spedizione_poste(ordine: Dict[str, Any], paperless: bool = False) -> Optional[str]:
    try:
        payload = build_poste_payload(ordine, paperless=paperless)
        log_debug("PAYLOAD WAYBILL:", json.dumps(payload, ensure_ascii=False))
        resp = requests.post(WAYBILL_URL, json=payload, headers=poste_headers(), timeout=30)
        resp.raise_for_status()
        result = resp.json()
        print(f"RISPOSTA POSTE: {result}")
        error_code = result.get("result", {}).get("errorCode")
        if error_code not in (0, "0", None):
            raise RuntimeError(result.get("result", {}).get("errorDescription") or f"Errore Poste {error_code}")
        ldv = ""
        waybills = result.get("waybills") or []
        if waybills and isinstance(waybills, list):
            first = waybills[0] or {}
            ldv = first.get("code") or first.get("waybill") or first.get("barcode") or ""
        if not ldv:
            raise RuntimeError("LDV non restituita")
        paese = (ordine.get("shipping_address", {}) or {}).get("country_code", "IT").upper()
        print(f"Spedizione creata! LDV: {ldv} - Paese: {paese}")
        return ldv
    except Exception as e:
        print(f"Errore creazione spedizione Poste: {e}")
        return None


def get_tracking_poste(ldv: str) -> Optional[str]:
    try:
        payload = {
            "arg0": {
                "shipmentsData": [{"waybillNumber": ldv, "lastTracingState": "S"}],
                "statusDescription": "E",
                "customerType": "DQ",
            }
        }
        resp = requests.post(TRACKING_URL, json=payload, headers=poste_headers(), timeout=15)
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


def aggiorna_tracking_shopify(ordine_id: str, order_number: Any, ldv: str) -> bool:
    try:
        headers = {
            "X-Shopify-Access-Token": SHOPIFY_TOKEN,
            "Content-Type": "application/json",
        }
        url_fo = f"https://{SHOPIFY_SHOP}/admin/api/{SHOPIFY_API_VERSION}/orders/{ordine_id}/fulfillment_orders.json"
        resp_fo = requests.get(url_fo, headers=headers, timeout=20)
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
        resp = requests.post(url_f, json=payload, headers=headers, timeout=20)
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


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"}), 200


@app.route("/debug/build-payload", methods=["POST"])
def debug_build_payload():
    ordine = request.get_json(silent=True) or {}
    try:
        payload = build_poste_payload(ordine, paperless=ENABLE_PAPERLESS)
        return jsonify(payload), 200
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/shipping-rates", methods=["POST"])
def shipping_rates():
    data = request.get_json(silent=True) or {}

    peso_grammi = 0
    paese = "IT"

    if "rate" in data:
        rate = data["rate"]
        paese = clean_text(rate.get("destination", {}).get("country", "IT"), max_len=2, upper=True)
        for item in rate.get("items", []):
            peso_grammi += int(item.get("grams", 500) or 500) * int(item.get("quantity", 1) or 1)

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
def tracking(ldv: str):
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

    try:
        validate_required_env()
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500

    ordine_id = str(ordine.get("id", ""))
    order_number = ordine.get("order_number", "")

    ordini_processati = carica_ordini()
    if ordine_id in ordini_processati:
        print(f"Ordine #{order_number} gia processato, ignoro duplicato")
        return jsonify({"status": "ok", "message": "already processed"}), 200

    shipping = ordine.get("shipping_address", {}) or {}
    paese = clean_text(shipping.get("country_code") or "IT", max_len=2, upper=True)

    if paese != "IT" and paese not in PAESE_ZONA:
        print(f"Ordine #{order_number} - paese {paese} non gestito, manuale")
        return jsonify({"status": "ok", "message": "paese non gestito"}), 200

    print(f"Ordine evaso: #{order_number} - {ordine.get('email', '')} - {paese}")

    ldv = crea_spedizione_poste(ordine, paperless=ENABLE_PAPERLESS)

    if ldv:
        ordini_processati.add(ordine_id)
        salva_ordini(ordini_processati)
        aggiorna_tracking_shopify(ordine_id, order_number, ldv)
        return jsonify({"status": "ok", "ldv": ldv}), 200

    return jsonify({"status": "error", "message": "Errore creazione spedizione"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
