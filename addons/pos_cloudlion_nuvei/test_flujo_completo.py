#!/usr/bin/env python3
"""
Simula el flujo completo de Odoo POS → Nuvei Cloud → POSSim.

1. Envía AUTQ (venta $1.00) via POST /services
2. Hace polling con RETR via POST /session cada 3 segundos
3. Cuando recibe serviceResponse (AUTP), muestra los datos del pago

Requisito: POSSim (simulador de terminal) debe estar corriendo y
conectado al sandbox de Nuvei.

Uso: python3 test_flujo_completo.py
"""
import json
import uuid
import time
import sys
import os
import requests
from datetime import datetime, timezone

# Credenciales desde variables de entorno (fallback a sandbox de desarrollo)
BASE_URL = os.environ.get('NUVEI_URL', 'https://terminal-sandbox.nuvei.com/omnichannel-cloudserver')
PID = os.environ.get('NUVEI_PID', 'CloudLionReg1')
TID = os.environ.get('NUVEI_TID', 'CloudLionTer1')
POS_KEY = os.environ.get('NUVEI_KEY', 'f78c3a2c-7d6d-4e07-89de-c9446ad4e4b7')
MERCHANT = os.environ.get('NUVEI_MERCHANT', '7800199838')
POLL_INTERVAL = 3  # segundos entre cada RETR
MAX_POLLS = 30     # máximo 30 intentos (90 segundos)


def ts():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def header(msg_func, eid):
    return {
        "messageFunction": msg_func,
        "protocolVersion": "2.0",
        "exchangeIdentification": eid,
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
    }


def post(endpoint, payload):
    url = f"{BASE_URL}/{endpoint}"
    r = requests.post(url, json=payload, headers={'Content-Type': 'application/json'}, timeout=30)
    return r.status_code, r.json()


# ── PASO 1: Enviar AUTQ (venta) ─────────────────────────────────────────────
def send_sale(amount):
    exchange_id = str(uuid.uuid4())
    payload = {
        "header": header("AUTQ", exchange_id),
        "serviceRequest": {
            "environment": {
                "merchant": {"identification": MERCHANT},
                "POI": {"identification": TID},
            },
            "context": {
                "saleContext": {
                    "cashierIdentification": "0001",
                    "invoiceNumber": "",
                    "identificationType": "",
                },
            },
            "serviceContent": "FSPQ",
            "paymentRequest": {
                "transactionType": "CRDP",
                "transactionDetails": {
                    "totalAmount": f"{amount:.2f}",
                    "MOTOIndicator": False,
                    "detailedAmount": {
                        "amountGoodsAndServices": f"{amount:.2f}",
                    },
                },
            },
        },
    }
    print(f"\n{'='*60}")
    print(f"  PASO 1: Enviando AUTQ (venta ${amount:.2f})")
    print(f"  Exchange ID: {exchange_id}")
    print(f"{'='*60}")

    status, data = post("services", payload)
    print(f"  → HTTP {status}")

    # Verificar respuesta inicial
    mgmt = data.get('sessionManagementResponse', {})
    resp = mgmt.get('sessionResponse', {}).get('response', '')
    print(f"  → Respuesta: {resp}")

    if resp == 'INPR':
        print(f"  ✓ Transacción aceptada, en proceso en el terminal")
    else:
        print(f"  ✗ Respuesta inesperada:")
        print(json.dumps(data, indent=2))

    return exchange_id, resp


# ── PASO 2: Polling con RETR ────────────────────────────────────────────────
def poll_status(exchange_id):
    print(f"\n{'='*60}")
    print(f"  PASO 2: Polling RETR (cada {POLL_INTERVAL}s, máx {MAX_POLLS} intentos)")
    print(f"  Esperando que el terminal procese la tarjeta...")
    print(f"{'='*60}")

    for i in range(MAX_POLLS):
        time.sleep(POLL_INTERVAL)

        payload = {
            "header": header("SASQ", str(uuid.uuid4())),
            "sessionManagementRequest": {
                "POSComponent": {
                    "POSGroupIdentification": {
                        "exchangeAction": "RETR",
                        "exchangeIdentification": exchange_id,
                    },
                },
            },
        }

        status, data = post("session", payload)

        # ¿Vino la respuesta final (serviceResponse)?
        if 'serviceResponse' in data:
            print(f"\n  ✓ ¡RESPUESTA FINAL RECIBIDA! (intento {i+1})")
            return True, data

        # ¿Vino sessionManagementResponse?
        if 'sessionManagementResponse' in data:
            mgmt = data['sessionManagementResponse']
            sess_resp = mgmt.get('sessionResponse', {}).get('response', '')
            txn = mgmt.get('transactionInProcess', {})
            txn_status = txn.get('transactionStatus', '')

            if txn_status:
                print(f"  [{i+1:2d}] txStatus={txn_status} resp={sess_resp}")
                if txn_status == 'RSPN':
                    # RSPN = respuesta lista, el próximo RETR debería traerla
                    print(f"       → Terminal completó, esperando respuesta...")
            else:
                print(f"  [{i+1:2d}] resp={sess_resp} (sin transacción en proceso)")

            # Verificar cancelación
            msg_fn = data.get('header', {}).get('messageFunction', '')
            if msg_fn == 'TXCN':
                print(f"\n  ✗ Transacción cancelada desde el terminal")
                return False, data

    print(f"\n  ✗ Timeout: {MAX_POLLS * POLL_INTERVAL} segundos sin respuesta")
    return False, None


# ── PASO 3: Parsear resultado ────────────────────────────────────────────────
def show_result(data):
    print(f"\n{'='*60}")
    print(f"  RESULTADO DEL PAGO")
    print(f"{'='*60}")

    svc = data.get('serviceResponse', {})
    payments = svc.get('paymentResponse', [])
    if not payments:
        print("  ✗ Sin paymentResponse en la respuesta")
        print(json.dumps(data, indent=2))
        return

    pay = payments[0] if isinstance(payments, list) else payments
    retail = pay.get('retailerPaymentResult', {})
    tx_resp = retail.get('transactionResponse', {})
    auth = tx_resp.get('authorisationResult', {})
    receipt = tx_resp.get('receiptDetails', {})

    resp_code = auth.get('responseToAuthorisation', {}).get('response', '')
    auth_code = auth.get('authorisationCode', '')
    card = receipt.get('mskPan', '')
    card_type = receipt.get('cardLbl', '')
    entry = receipt.get('cardDataNtryMd', '')
    approval = receipt.get('apprdeclISO', '')
    sale_ref = pay.get('saleTransactionIdentification', {}).get('transactionReference', '')
    term_ref = pay.get('POITransactionIdentification', {}).get('transactionReference', '')
    amount = retail.get('transactionDetails', {}).get('totalAmount', '')

    print(f"  Respuesta:    {resp_code}")
    print(f"  Auth Code:    {auth_code}")
    print(f"  Tarjeta:      {card}")
    print(f"  Tipo:         {card_type}")
    print(f"  Entrada:      {entry}")
    print(f"  Aprobación:   {approval}")
    print(f"  Sale Ref:     {sale_ref}")
    print(f"  Terminal Ref: {term_ref}")
    print(f"  Monto:        ${amount}")

    # Recibos
    receipts = pay.get('receipt', [])
    for r in receipts:
        qualifier = r.get('documentQualifier', '')
        name = 'MERCHANT' if qualifier == 'HRCP' else 'CUSTOMER' if qualifier == 'CRCP' else qualifier
        print(f"\n  ── Recibo {name} ──")
        content = r.get('outputContent', '')
        # El recibo viene separado por | — convertir a líneas
        for line in content.split('|'):
            stripped = line.strip()
            if stripped:
                print(f"  {stripped}")

    if resp_code == 'APPR':
        print(f"\n  ✓ PAGO APROBADO")
    else:
        print(f"\n  ✗ PAGO NO APROBADO: {resp_code}")


# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    amount = float(sys.argv[1]) if len(sys.argv) > 1 else 1.00

    print()
    print("╔" + "═" * 58 + "╗")
    print("║" + " FLUJO COMPLETO: Odoo POS → Nuvei Cloud → POSSim ".center(58) + "║")
    print("╚" + "═" * 58 + "╝")
    print(f"\n  POSSim debe estar corriendo y conectado al sandbox")
    print(f"  Monto: ${amount:.2f}")

    # Paso 1: Enviar venta
    exchange_id, resp = send_sale(amount)
    if resp != 'INPR':
        print("\n  ✗ No se pudo iniciar la transacción")
        sys.exit(1)

    # Paso 2: Polling
    success, data = poll_status(exchange_id)

    # Paso 3: Mostrar resultado
    if success and data:
        show_result(data)
    else:
        print("\n  ✗ No se obtuvo respuesta del terminal")

    print()
