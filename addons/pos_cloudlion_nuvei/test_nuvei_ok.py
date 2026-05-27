#!/usr/bin/env python3
"""
Pruebas validadas contra sandbox Nuvei — SOLO las que funcionan.
SIN wrapper OCserviceRequest / OCsessionManagementRequest.
URL: https://terminal-sandbox.nuvei.com/omnichannel-cloudserver

Resultados confirmados:
  NOTI (POS)  → HTTP 201, response=APPR  ✓
  NOTI (TER)  → HTTP 201, response=APPR  ✓
  RETR        → HTTP 201, response=NOTF  ✓ (no hay tx pendiente)
  SALE (AUTQ) → HTTP 200, response=INPR  ✓ (tx aceptada, en proceso)

Uso:
  python3 test_nuvei_ok.py
"""
import json
import uuid
import requests
import os
from datetime import datetime, timezone

# Credenciales desde variables de entorno (fallback a sandbox de desarrollo)
BASE_URL = os.environ.get('NUVEI_URL', 'https://terminal-sandbox.nuvei.com/omnichannel-cloudserver')
TID = os.environ.get('NUVEI_TID', 'CloudLionTer1')
PID = os.environ.get('NUVEI_PID', 'CloudLionReg1')
POS_KEY = os.environ.get('NUVEI_KEY', 'f78c3a2c-7d6d-4e07-89de-c9446ad4e4b7')
TER_KEY = os.environ.get('NUVEI_TER_KEY', '021e9479-8849-4753-80be-356ca413ea38')
MERCHANT = os.environ.get('NUVEI_MERCHANT', '7800199838')


def ts():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def post(endpoint, payload, label):
    url = f"{BASE_URL}/{endpoint}"
    print(f"\n{'─'*60}\n  {label}\n  POST {url}\n{'─'*60}")
    print(json.dumps(payload, indent=2))
    r = requests.post(url, json=payload, headers={'Content-Type': 'application/json'}, timeout=30)
    print(f"\n  → HTTP {r.status_code}")
    try:
        data = r.json()
        print(json.dumps(data, indent=2))
        return data
    except Exception:
        print(f"  → {r.text[:200]}")
        return None


# ── 1. NOTI desde POS (valida credenciales POS) ─────────────────────────────
def test_noti_pos():
    return post("session", {
        "header": {
            "messageFunction": "SASQ",
            "protocolVersion": "2.0",
            "exchangeIdentification": str(uuid.uuid4()),
            "creationDateTime": ts(),
            "initiatingParty": {
                "identification": PID,
                "type": "PID",
                "shortName": "Cash Register ID",
                "authenticationKey": POS_KEY,
            },
            "recipientParty": {
                "identification": TID,
                "type": "TID",
                "shortName": "Terminal ID",
            },
        },
        "sessionManagementRequest": {
            "POSComponent": {
                "POSGroupIdentification": {
                    "exchangeAction": "NOTI",
                },
            },
        },
    }, "NOTI — POS (PID) → Espera APPR")


# ── 2. NOTI desde Terminal (heartbeat terminal) ─────────────────────────────
def test_noti_ter():
    return post("session", {
        "header": {
            "messageFunction": "SASQ",
            "protocolVersion": "2.0",
            "exchangeIdentification": str(uuid.uuid4()),
            "creationDateTime": ts(),
            "initiatingParty": {
                "identification": TID,
                "type": "TID",
                "authenticationKey": TER_KEY,
            },
        },
        "sessionManagementRequest": {
            "POIComponent": {
                "POIGroupIdentification": {
                    "exchangeAction": "NOTI",
                },
                "state": "IDLE",
            },
        },
    }, "NOTI — Terminal (TID) → Espera APPR")


# ── 3. RETR polling (consultar estado de transacción) ────────────────────────
def test_retr(exchange_id=None):
    return post("session", {
        "header": {
            "messageFunction": "SASQ",
            "protocolVersion": "2.0",
            "exchangeIdentification": str(uuid.uuid4()),
            "creationDateTime": ts(),
            "initiatingParty": {
                "identification": PID,
                "type": "PID",
                "shortName": "Cash Register ID",
                "authenticationKey": POS_KEY,
            },
            "recipientParty": {
                "identification": TID,
                "type": "TID",
                "shortName": "Terminal ID",
            },
        },
        "sessionManagementRequest": {
            "POSComponent": {
                "POSGroupIdentification": {
                    "exchangeAction": "RETR",
                    "exchangeIdentification": exchange_id or str(uuid.uuid4()),
                },
            },
        },
    }, "RETR — Polling → Espera NOTF o transacción")


# ── 4. SALE $1.00 (pago) ────────────────────────────────────────────────────
def test_sale():
    return post("services", {
        "header": {
            "messageFunction": "AUTQ",
            "protocolVersion": "2.0",
            "exchangeIdentification": str(uuid.uuid4()),
            "creationDateTime": ts(),
            "initiatingParty": {
                "identification": PID,
                "type": "PID",
                "shortName": "Cash Register ID",
                "authenticationKey": POS_KEY,
            },
            "recipientParty": {
                "identification": TID,
                "type": "TID",
                "shortName": "Terminal ID",
            },
        },
        "serviceRequest": {
            "environment": {
                "merchant": {"identification": MERCHANT},
                "POI": {"identification": TID},
            },
            "context": {
                "saleContext": {
                    "cashierIdentification": "",
                    "invoiceNumber": "",
                    "identificationType": "",
                },
            },
            "serviceContent": "FSPQ",
            "paymentRequest": {
                "transactionType": "CRDP",
                "transactionDetails": {
                    "totalAmount": "1.00",
                    "MOTOIndicator": False,
                    "detailedAmount": {
                        "amountGoodsAndServices": "1.00",
                    },
                },
            },
        },
    }, "SALE $1.00 (AUTQ) → Espera INPR")


if __name__ == "__main__":
    print("\n  NUVEI REST — Solo pruebas que funcionan (SIN wrapper)\n")
    test_noti_pos()
    test_noti_ter()
    test_retr()
    test_sale()
    print()
