from __future__ import annotations

"""
/app.py

Shopify → Poste Delivery Business integration (Railway-ready).

Key fixes for International Plus (APT001013):
- Never send `data.services` (Poste rejects it for international if empty/nonconforming).
- Never send `items[].description` (field not allowed for APT001013 items).
- `international.contentCode` is parsed from `international/nation/details` under `carriers.*.content`.
- `data.description` is mandatory for international; also duplicated at `waybill.description` for safety.
- Fail fast if `contentCode` cannot be determined (returns "999").
"""

import json
import math
import os
import time
from typing import Any, Dict, List, Optional

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# ENV
# ──────────────────────────────────────────────────────────────────────────────
POSTE_CLIENT_ID = os.environ.get("POSTE_CLIENT_ID", "")
POSTE_SECRET_ID = os.environ.get("POSTE_SECRET_ID", "")
POSTE_COST_CENTER = os.environ.get("POSTE_COST_CENTER", "CDC-00080197")
POSTE_CONTRACT_CODE = os.environ.get("POSTE_CONTRACT_CODE", "")  # optional
POSTE_PACKAGING_CODE = os.environ.get("POSTE_PACKAGING_CODE", "CC")
POSTE_INTL_DESCRIPTION = os.environ.get("POSTE_INTL_DESCRIPTION", "Merce generica")

SHOPIFY_TOKEN = os.environ.get("SHOPIFY_TOKEN", "")
SHOPIFY_SHOP = os.environ.get("SHOPIFY_SHOP", "")
SHOPIFY_API_VERSION = os.environ.get("SHOPIFY_API_VERSION", "2024-04")

# optional overrides
POSTE_INTL_CONTENT_CODE = os.environ.get("POSTE_INTL_CONTENT_CODE", "")  # set to force a specific contentCode
POSTE_TARIC = os.environ.get("POSTE_TARIC", "2313200310")
POSTE_ORIGIN_COUNTRY = os.environ.get("POSTE_ORIGIN_COUNTRY", "IT")

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
INTERNATIONAL_NATION_DETAILS_URL = (
    "https://apiw.gp.posteitaliane.it/gp/internet/postalandlogistics/parcel/international/nation/details"
)
WAYBILL_SERVICES_URL = "https://apiw.gp.posteitaliane.it/gp/internet/postalandlogistics/parcel/waybill/services"

SCOPE_PRODUZIONE = "https://postemarketplace.onmicrosoft.com/d6a78063-5570-4a87-bbd7-07326e6855d1/.default"


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────
def log_debug(*args: Any) -> None:
    try:
        print(*args)
    except Exception:
        pass


def is_blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def clean_text(value: Any, max_len: int = 0, upper: bool = False) -> str:
    s = "" if value is None else str(value).strip()
    if upper:
        s = s.upper()
    if max_len and len(s) > max_len:
        s = s[:max_len]
    return s


def digits_only(value: Any, plus_allowed: bool = False, max_len: int = 0) -> str:
    s = clean_text(value)
    prefix = ""
    if plus_allowed and s.startswith("+"):
        prefix = "+"
        s = s[1:]
    out = prefix + "".join(ch for ch in s if ch.isdigit())
    if max_len and len(out) > max_len:
        out = out[:max_len]
    return out


def round_up_kg_from_grams(grams: int) -> float:
    return math.ceil((grams / 1000.0) * 100) / 100.0


def get_order_weight_grams(order: Dict[str, Any]) -> int:
    total = 0
    for li in order.get("line_items", []) or []:
        grams = int(li.get("grams") or 0)
        qty = int(li.get("quantity") or 0)
        total += grams * qty
    return total if total > 0 else 100


# ──────────────────────────────────────────────────────────────────────────────
# POSTE AUTH
# ──────────────────────────────────────────────────────────────────────────────
_token_cache: Dict[str, Any] = {"token": None, "exp": 0}


def poste_token() -> str:
    now = int(time.time())
    if _token_cache.get("token") and now < int(_token_cache.get("exp", 0)) - 60:
        return str(_token_cache["token"])

    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "grant_type": "client_credentials",
        "client_id": POSTE_CLIENT_ID,
        "client_secret": POSTE_SECRET_ID,
        "scope": SCOPE_PRODUZIONE,
    }
    resp = requests.post(AUTH_URL, data=data, headers=headers, timeout=30)
    resp.raise_for_status()
    payload = resp.json() or {}
    token = payload.get("access_token")
    expires_in = int(payload.get("expires_in", 3600))
    if not token:
        raise RuntimeError("Token Poste non ottenuto")
    _token_cache["token"] = token
    _token_cache["exp"] = now + expires_in
    return str(token)


def poste_headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {poste_token()}", "Content-Type": "application/json"}


# ──────────────────────────────────────────────────────────────────────────────
# INTERNATIONAL LOOKUPS (cached)
# ──────────────────────────────────────────────────────────────────────────────
ISO2_TO_ISO4 = {
    "IT": "ITA1",
    "DE": "GER1",
    "FR": "FRA1",
    "ES": "ESP1",
    "NL": "NLD1",
    "BE": "BEL1",
    "AT": "AUT1",
    "PT": "PRT1",
    "PL": "POL1",
    "SE": "SWE1",
    "DK": "DNK1",
    "FI": "FIN1",
    "IE": "IRL1",
    "CZ": "CZE1",
    "SK": "SVK1",
    "HU": "HUN1",
    "RO": "ROU1",
    "BG": "BGR1",
    "HR": "HRV1",
    "SI": "SVN1",
    "LT": "LTU1",
    "LV": "LVA1",
    "EE": "EST1",
    "LU": "LUX1",
    "MT": "MLT1",
    "CY": "CYP1",
    "GR": "GRC1",
}

_nations_cache: Dict[str, Any] = {"at": 0, "data": []}
_details_cache: Dict[str, Any] = {}


def get_international_nations(force: bool = False) -> List[Dict[str, Any]]:
    now = int(time.time())
    if not force and _nations_cache["data"] and now - int(_nations_cache["at"]) < 6 * 3600:
        return list(_nations_cache["data"])

    resp = requests.get(INTERNATIONAL_NATIONS_URL, headers=poste_headers(), timeout=30)
    resp.raise_for_status()
    result = resp.json() or {}
    nations = result.get("nations") or result.get("result") or result.get("return") or []
    if not isinstance(nations, list):
        nations = []
    _nations_cache["at"] = now
    _nations_cache["data"] = nations
    return nations


def get_country_info_by_iso2(iso2: str) -> Optional[Dict[str, Any]]:
    iso2 = clean_text(iso2, max_len=2, upper=True)
    for n in get_international_nations():
        if clean_text(n.get("iso2"), max_len=2, upper=True) == iso2:
            return n
    return None


def get_country_product_details(country_code: str, product_code: str) -> Dict[str, Any]:
    cache_key = f"{country_code}:{product_code}"
    if cache_key in _details_cache:
        return _details_cache[cache_key]

    payload = {"countryCode": country_code, "productCode": product_code}
    resp = requests.post(INTERNATIONAL_NATION_DETAILS_URL, json=payload, headers=poste_headers(), timeout=30)
    resp.raise_for_status()
    result = resp.json() or {}
    details = result.get("details") or result.get("return") or result
    if not isinstance(details, dict):
        details = {}
    _details_cache[cache_key] = details
    return details


def choose_content_code(country_details: Dict[str, Any]) -> str:
    forced = clean_text(POSTE_INTL_CONTENT_CODE, max_len=3)
    if forced:
        return forced

    carriers = country_details.get("carriers") or {}
    if not isinstance(carriers, dict):
        carriers = {}

    # Prefer allowed content codes
    for _, carrier_payload in carriers.items():
        content_list = (carrier_payload or {}).get("content") or []
        for row in content_list:
            allowed = str(row.get("content_allowed", "")).lower()
            if row.get("content_allowed") in (True, 1, "1") or allowed in {"true", "yes", "y", "s"}:
                code = clean_text(row.get("content_code"), max_len=3)
                if code:
                    return code

    # Fallback: first content_code available
    for _, carrier_payload in carriers.items():
        content_list = (carrier_payload or {}).get("content") or []
        for row in content_list:
            code = clean_text(row.get("content_code"), max_len=3)
            if code:
                return code

    return "999"


# ──────────────────────────────────────────────────────────────────────────────
# PAYLOAD BUILDERS
# ──────────────────────────────────────────────────────────────────────────────
def build_poste_items(order: Dict[str, Any], total_weight_grams: int) -> List[Dict[str, Any]]:
    line_items = order.get("line_items", []) or []
    if not line_items:
        return [
            {
                "itemNumber": "1",
                "taric": clean_text(POSTE_TARIC, max_len=10),
                "totalValue": "0",
                "quantity": "1",
                "totalWeight": str(total_weight_grams),
                "originCountry": clean_text(POSTE_ORIGIN_COUNTRY, max_len=2, upper=True),
            }
        ]

    total_qty = sum(int(li.get("quantity") or 0) for li in line_items) or 1
    weight_per_unit = max(1, int(total_weight_grams / total_qty))

    items: List[Dict[str, Any]] = []
    idx = 1
    for li in line_items:
        qty = int(li.get("quantity") or 0) or 1
        price = li.get("price") or "0"
        try:
            total_value = str(int(float(price) * qty))
        except Exception:
            total_value = "0"
        items.append(
            {
                "itemNumber": str(idx),
                "taric": clean_text(POSTE_TARIC, max_len=10),
                "totalValue": total_value,
                "quantity": str(qty),
                "totalWeight": str(weight_per_unit * qty),
                "originCountry": clean_text(POSTE_ORIGIN_COUNTRY, max_len=2, upper=True),
            }
        )
        idx += 1
    return items


def infer_receiver_type(order: Dict[str, Any], weight_kg: float) -> str:
    return "retailDelivery"


def build_receiver(order: Dict[str, Any], country_code: str, country_name: str, iso2: str) -> Dict[str, str]:
    shipping = order.get("shipping_address", {}) or {}

    full_name = clean_text(f"{shipping.get('first_name', '')} {shipping.get('last_name', '')}", max_len=35)
    company = clean_text(shipping.get("company"), max_len=35)
    contact_name = full_name or company or "CLIENTE"
    name_surname = company or full_name or "CLIENTE"

    address1 = clean_text(shipping.get("address1"), max_len=40)
    address2 = clean_text(shipping.get("address2"), max_len=40)
    address = clean_text(" ".join([p for p in [address1, address2] if p]), max_len=40)

    phone = digits_only(shipping.get("phone") or order.get("phone"), plus_allowed=True, max_len=15)
    if not phone:
        phone = digits_only(MITTENTE.get("phone"), plus_allowed=True, max_len=15)

    email = clean_text(order.get("email") or MITTENTE.get("email"), max_len=50)

    return {
        "zipCode": clean_text(shipping.get("zip"), max_len=7),
        "addressId": "",
        "streetNumber": clean_text(shipping.get("address2"), max_len=4),
        "city": clean_text(shipping.get("city"), max_len=30, upper=True),
        "address": address,
        "country": country_code,
        "countryName": clean_text(country_name or shipping.get("country") or iso2, max_len=30),
        "nameSurname": name_surname,
        "contactName": contact_name,
        "province": clean_text(shipping.get("province_code"), max_len=2, upper=True),
        "email": email,
        "phone": phone,
        "cellphone": "",
        "note1": "",
        "note2": "",
    }


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


def build_poste_payload(order: Dict[str, Any], paperless: bool = False) -> Dict[str, Any]:
    shipping = order.get("shipping_address", {}) or {}
    iso2 = clean_text(shipping.get("country_code") or "IT", max_len=2, upper=True)

    weight_g = get_order_weight_grams(order)
    weight_kg = round_up_kg_from_grams(weight_g)

    if iso2 == "IT":
        product_code = "APT000901"
        country_code = "ITA1"
        country_name = "Italia"
    else:
        product_code = "APT001013"
        info = get_country_info_by_iso2(iso2)
        if not info:
            raise ValueError(f"Paese {iso2} non restituito da international/nations")
        if not info.get("active"):
            raise ValueError(f"Paese {iso2} non attivo su Poste")
        if product_code not in (info.get("products") or []):
            raise ValueError(f"Prodotto {product_code} non disponibile per il paese {iso2}")

        country_code = clean_text(info.get("iso4") or ISO2_TO_ISO4.get(iso2, f"{iso2}1"), max_len=4, upper=True)
        country_name = clean_text(info.get("name") or shipping.get("country") or iso2, max_len=30)

    receiver = build_receiver(order, country_code, country_name, iso2)

    declared = [{"weight": str(weight_g), "height": "10", "length": "30", "width": "25"}]

    # Description: never empty, <= 30
    first_title = ""
    try:
        first_title = clean_text(((order.get("line_items") or [{}])[0]).get("title"), max_len=60)
    except Exception:
        first_title = ""
    shipment_description = clean_text(first_title or POSTE_INTL_DESCRIPTION or "Merce", max_len=30) or "Merce"

    data_block: Dict[str, Any] = {
        "declared": declared,
        "sender": MITTENTE,
        "receiver": receiver,
    }

    if iso2 == "IT":
        data_block["content"] = clean_text(POSTE_INTL_DESCRIPTION, max_len=30)
    else:
        details = get_country_product_details(country_code, product_code)
        content_code = choose_content_code(details)
        log_debug("contentCode scelto:", content_code)

        data_block["description"] = shipment_description
        data_block["content"] = shipment_description  # compat
        data_block["packagingCode"] = clean_text(POSTE_PACKAGING_CODE, max_len=2, upper=True)

        items = build_poste_items(order, weight_g)
        # Ensure NO description in items
        for it in items:
            it.pop("description", None)
        data_block["items"] = items

        data_block["international"] = {
            "receiverType": infer_receiver_type(order, weight_kg),
            "contentCode": content_code,
        }

        # Hard strip: Poste rejects services for intl unless a conform serviceMap is provided.
        data_block.pop("services", None)

    waybill: Dict[str, Any] = {
        "clientReferenceId": str(order.get("order_number", order.get("id", "")))[:25],
        "printFormat": "A4",
        "product": product_code,
        "data": data_block,
    }

    # Some validators expect it also at waybill-level for intl
    if iso2 != "IT":
        waybill["description"] = shipment_description

    payload: Dict[str, Any] = {
        "costCenterCode": POSTE_COST_CENTER,
        "paperless": bool(paperless),
        "shipmentDate": time.strftime("%Y-%m-%dT%H:%M:%S.000+0000", time.gmtime()),
        "waybills": [waybill],
    }
    if POSTE_CONTRACT_CODE:
        payload["contractCode"] = POSTE_CONTRACT_CODE

    return payload


# ──────────────────────────────────────────────────────────────────────────────
# POSTE CALLS
# ──────────────────────────────────────────────────────────────────────────────
def crea_spedizione_poste(order: Dict[str, Any], paperless: bool = False) -> Optional[str]:
    try:
        payload = build_poste_payload(order, paperless=paperless)
        log_debug("PAYLOAD WAYBILL:", json.dumps(payload, ensure_ascii=False))

        resp = requests.post(WAYBILL_URL, json=payload, headers=poste_headers(), timeout=30)
        resp.raise_for_status()
        result = resp.json() or {}
        log_debug("RISPOSTA POSTE:", result)

        error_code = (result.get("result") or {}).get("errorCode")
        if error_code not in (0, "0", None):
            raise RuntimeError((result.get("result") or {}).get("errorDescription") or f"Errore Poste {error_code}")

        waybills = result.get("waybills") or []
        ldv = ""
        if isinstance(waybills, list) and waybills:
            first = waybills[0] or {}
            ldv = first.get("code") or first.get("waybill") or first.get("barcode") or ""
        if not ldv:
            raise RuntimeError("LDV non restituita")

        return str(ldv)
    except Exception as exc:
        log_debug("Errore creazione spedizione Poste:", exc)
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
        result = resp.json() or {}
        shipment = (result.get("return") or {}).get("messages", [{}])[0]
        events = shipment.get("tracking", []) or []
        if events:
            last = events[-1]
            return str(last.get("statusDescription", "")) or None
        return None
    except Exception as exc:
        log_debug("Errore tracking Poste:", exc)
        return None


# ──────────────────────────────────────────────────────────────────────────────
# SHOPIFY
# ──────────────────────────────────────────────────────────────────────────────
def aggiorna_tracking_shopify(order_id: str, ldv: str) -> bool:
    try:
        headers = {"X-Shopify-Access-Token": SHOPIFY_TOKEN, "Content-Type": "application/json"}

        url_fo = f"https://{SHOPIFY_SHOP}/admin/api/{SHOPIFY_API_VERSION}/orders/{order_id}/fulfillment_orders.json"
        resp_fo = requests.get(url_fo, headers=headers, timeout=20)
        resp_fo.raise_for_status()
        fulfillment_orders = resp_fo.json().get("fulfillment_orders", []) or []
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
                "tracking_info": {"number": ldv, "company": "Poste Italiane", "url": ""},
                "notify_customer": True,
            }
        }
        resp = requests.post(url_f, headers=headers, json=payload, timeout=20)
        resp.raise_for_status()
        return True
    except Exception as exc:
        log_debug("Errore aggiornamento tracking Shopify:", exc)
        return False


# ──────────────────────────────────────────────────────────────────────────────
# ROUTES
# ──────────────────────────────────────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health() -> Any:
    return jsonify({"ok": True})


@app.route("/shopify/webhook/fulfillment", methods=["POST"])
def shopify_webhook_fulfillment() -> Any:
    try:
        validate_required_env()
        order = request.get_json(force=True) or {}

        order_id = str(order.get("id") or "")
        if not order_id:
            return jsonify({"ok": False, "error": "Missing order id"}), 400

        iso2 = clean_text(((order.get("shipping_address") or {}).get("country_code")) or "IT", max_len=2, upper=True)
        log_debug("Ordine evaso:", order.get("order_number") or order.get("name") or order.get("id"), "-", iso2)

        ldv = crea_spedizione_poste(order, paperless=False)
        if not ldv:
            return jsonify({"ok": False, "error": "Creazione spedizione fallita"}), 500

        updated = aggiorna_tracking_shopify(order_id, ldv)
        return jsonify({"ok": True, "ldv": ldv, "shopifyUpdated": updated})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
