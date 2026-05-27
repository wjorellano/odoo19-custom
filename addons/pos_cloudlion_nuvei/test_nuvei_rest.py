#!/usr/bin/env python3
"""
Script de prueba de sesión Nuvei via REST API (HTTPS 443).
=========================================================
URL descubierta (validada con curl desde POSSim):
  https://terminal-sandbox.nuvei.com/omnichannel-cloudserver/

IMPORTANTE — Hallazgos del POSSim:
  1. La API REST NO usa los wrappers "OCserviceRequest" / "OCsessionManagementRequest".
     Se envían directamente "header" + "serviceRequest" (o "sessionManagementRequest") como
     claves raíz del JSON. El servidor responde 400 "element is missing" si se usa wrapper.
  2. El terminal (POSSim) se conecta via SSL raw socket (18080) y SÍ usa wrappers.
     El POS (Odoo) se conecta via REST HTTPS (443) y NO usa wrappers.
  3. El auth key del POS (f78c3a2c...) es diferente al del terminal (021e9479...).

Endpoints REST:
  POST /omnichannel-cloudserver/services  → header + serviceRequest
  POST /omnichannel-cloudserver/session   → header + sessionManagementRequest
  POST /omnichannel-cloudserver/report    → header + reportRequest

Uso:
  python3 test_nuvei_rest.py          # Ejecuta todas las pruebas
  python3 test_nuvei_rest.py stck     # Solo Status Check
  python3 test_nuvei_rest.py noti     # Solo Heartbeat
  python3 test_nuvei_rest.py sale     # Solo pago de prueba $1.00
  python3 test_nuvei_rest.py retr     # Polling (necesita exchange_id previo)
"""
import json
import uuid
import sys
import os
import requests
from datetime import datetime, timezone

# ─── Configuración ──────────────────────────────────────────────────────────
# Credenciales desde variables de entorno (fallback a sandbox de desarrollo)
BASE_URL = os.environ.get('NUVEI_URL', 'https://terminal-sandbox.nuvei.com/omnichannel-cloudserver')

# Credenciales del POS (Register) — para REST API
TID = os.environ.get('NUVEI_TID', 'CloudLionTer1')       # Terminal ID
PID = os.environ.get('NUVEI_PID', 'CloudLionReg1')       # POS/Register ID
POS_AUTH_KEY = os.environ.get('NUVEI_KEY', 'f78c3a2c-7d6d-4e07-89de-c9446ad4e4b7')   # Auth key del POS
TER_AUTH_KEY = os.environ.get('NUVEI_TER_KEY', '021e9479-8849-4753-80be-356ca413ea38')    # Auth key del Terminal
MERCHANT = os.environ.get('NUVEI_MERCHANT', '7800199838')
PROTOCOL_VERSION = "2.0"
TIMEOUT = 30


def timestamp():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def post_nuvei(endpoint, payload, label=""):
    """Envía POST al servidor Nuvei y muestra resultado."""
    url = f"{BASE_URL}/{endpoint}"
    print(f"\n{'=' * 65}")
    print(f"  {label}")
    print(f"  POST {url}")
    print(f"{'=' * 65}")
    print(f"\n  Request JSON:")
    print(json.dumps(payload, indent=2))

    try:
        resp = requests.post(
            url,
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=TIMEOUT,
        )
        print(f"\n  HTTP Status: {resp.status_code}")
        print(f"  Content-Type: {resp.headers.get('Content-Type', '?')}")
        print(f"  Response length: {len(resp.content)} bytes")

        # Intentar parsear JSON
        try:
            data = resp.json()
            print(f"\n  Response JSON:")
            print(json.dumps(data, indent=2))
            return resp.status_code in (200, 201), data
        except Exception:
            text = resp.text
            print(f"\n  Response Text: {text[:500]}")
            return False, text

    except requests.exceptions.ConnectionError as e:
        print(f"\n  ✗ Error de conexión: {e}")
        return False, None
    except requests.exceptions.Timeout:
        print(f"\n  ✗ Timeout ({TIMEOUT}s)")
        return False, None
    except Exception as e:
        print(f"\n  ✗ Error: {type(e).__name__}: {e}")
        return False, None


# ─── Pruebas de sesión (/session) ───────────────────────────────────────────

def test_stck():
    """
    Prueba STCK (Status Check) — perspectiva POS, SIN wrapper.
    Valida credenciales POS y consulta estado del terminal.
    """
    # REST: SIN wrapper OCsessionManagementRequest, directo header + sessionManagementRequest
    payload = {
        "header": {
            "messageFunction": "SASQ",
            "protocolVersion": PROTOCOL_VERSION,
            "exchangeIdentification": str(uuid.uuid4()),
            "creationDateTime": timestamp(),
            "initiatingParty": {
                "identification": PID,
                "type": "PID",
                "shortName": "Cash Register ID",
                "authenticationKey": POS_AUTH_KEY,
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
                    "exchangeAction": "STCK",
                },
            },
        },
    }
    return post_nuvei("session", payload, "PRUEBA: STCK (Status Check) — POS auth, SIN wrapper")


def test_noti_pos():
    """
    Prueba NOTI — desde la perspectiva del POS (PID), SIN wrapper.
    """
    payload = {
        "header": {
            "messageFunction": "SASQ",
            "protocolVersion": PROTOCOL_VERSION,
            "exchangeIdentification": str(uuid.uuid4()),
            "creationDateTime": timestamp(),
            "initiatingParty": {
                "identification": PID,
                "type": "PID",
                "shortName": "Cash Register ID",
                "authenticationKey": POS_AUTH_KEY,
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
    }
    return post_nuvei("session", payload, "PRUEBA: NOTI — POS auth (PID), SIN wrapper")


def test_noti_ter():
    """
    Prueba NOTI — simulando terminal (TID) como en POSSim, SIN wrapper.
    Este es el heartbeat que envía el terminal regularmente.
    """
    payload = {
        "header": {
            "messageFunction": "SASQ",
            "protocolVersion": PROTOCOL_VERSION,
            "exchangeIdentification": str(uuid.uuid4()),
            "creationDateTime": timestamp(),
            "initiatingParty": {
                "identification": TID,
                "type": "TID",
                "authenticationKey": TER_AUTH_KEY,
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
    }
    return post_nuvei("session", payload, "PRUEBA: NOTI — Terminal auth (TID), SIN wrapper")


def test_retr(exchange_id=None):
    """
    Prueba RETR (Retrieve) — polling del POS para obtener respuesta del terminal.
    Necesita un exchange_id de una transacción previa.
    """
    if not exchange_id:
        exchange_id = str(uuid.uuid4())  # dummy para probar estructura
    payload = {
        "header": {
            "messageFunction": "SASQ",
            "protocolVersion": PROTOCOL_VERSION,
            "exchangeIdentification": str(uuid.uuid4()),
            "creationDateTime": timestamp(),
            "initiatingParty": {
                "identification": PID,
                "type": "PID",
                "shortName": "Cash Register ID",
                "authenticationKey": POS_AUTH_KEY,
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
                    "exchangeIdentification": exchange_id,
                },
            },
        },
    }
    return post_nuvei("session", payload, f"PRUEBA: RETR (Polling) — exchange={exchange_id[:12]}...")


# ─── Pruebas de servicio (/services) ───────────────────────────────────────

def test_sale():
    """
    Prueba AUTQ (Sale $1.00) — SIN wrapper OCserviceRequest.
    Tiene que funcionar: ya se validó con HTTP 200 en la prueba anterior.
    """
    payload = {
        "header": {
            "messageFunction": "AUTQ",
            "protocolVersion": PROTOCOL_VERSION,
            "exchangeIdentification": str(uuid.uuid4()),
            "creationDateTime": timestamp(),
            "initiatingParty": {
                "identification": PID,
                "type": "PID",
                "shortName": "Cash Register ID",
                "authenticationKey": POS_AUTH_KEY,
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
    }
    return post_nuvei("services", payload, "PRUEBA: AUTQ (Sale $1.00) — SIN wrapper")


# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    print()
    print("╔" + "═" * 63 + "╗")
    print("║" + " NUVEI REST API TEST — SIN WRAPPERS ".center(63) + "║")
    print("║" + f" {BASE_URL} ".center(63) + "║")
    print("╚" + "═" * 63 + "╝")
    print()
    print(f"  TID:          {TID}")
    print(f"  PID:          {PID}")
    print(f"  POS Auth Key: {POS_AUTH_KEY[:8]}...{POS_AUTH_KEY[-4:]}")
    print(f"  TER Auth Key: {TER_AUTH_KEY[:8]}...{TER_AUTH_KEY[-4:]}")
    print(f"  Merchant:     {MERCHANT}")

    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    results = []

    if mode in ("stck", "all"):
        ok, data = test_stck()
        results.append(("STCK (POS→session)", ok, data))

    if mode in ("noti", "noti_pos", "all"):
        ok, data = test_noti_pos()
        results.append(("NOTI POS (PID→session)", ok, data))

    if mode in ("noti_ter", "all"):
        ok, data = test_noti_ter()
        results.append(("NOTI TER (TID→session)", ok, data))

    if mode in ("retr", "all"):
        eid = sys.argv[2] if len(sys.argv) > 2 else None
        ok, data = test_retr(eid)
        results.append(("RETR (polling)", ok, data))

    if mode in ("sale", "all"):
        ok, data = test_sale()
        results.append(("SALE (AUTQ→services)", ok, data))

    # Resumen
    print(f"\n{'=' * 65}")
    print("  RESUMEN")
    print(f"{'=' * 65}")
    for name, ok, data in results:
        status = "✓ OK" if ok else "✗ FAIL"
        detail = ""
        if isinstance(data, dict):
            # Buscar response code en la respuesta
            sr = data.get("sessionManagementResponse", {}).get("sessionResponse", {})
            if sr:
                detail = f" → response={sr.get('response', '?')}"
            elif "header" in data:
                mf = data["header"].get("messageFunction", "?")
                detail = f" → msgFunc={mf}"
        elif isinstance(data, str):
            detail = f" → {data[:50].strip()}"
        print(f"    {status}  {name}{detail}")
    print()


if __name__ == "__main__":
    main()
