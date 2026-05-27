import requests
import json
import uuid
import os
from datetime import datetime

# --- CONFIGURACIÓN ---
# Credenciales desde variables de entorno (fallback a sandbox de desarrollo)
URL_CLOUDSVR = os.environ.get('NUVEI_URL', 'https://terminal-sandbox.nuvei.com/services')

MERCHANT_ID = os.environ.get('NUVEI_MERCHANT', '7800199838')
TERMINAL_ID = os.environ.get('NUVEI_TID', 'CloudLionTer1')
REGISTER_ID = os.environ.get('NUVEI_PID', 'CloudLionReg1')
AUTH_KEY = os.environ.get('NUVEI_KEY', 'f78c3a2c-7d6d-4e07-89de-c9446ad4e4b7')

def test_pago_nuvei(monto):
    exchange_id = str(uuid.uuid4())
    # Formato de fecha ISO20022 (Sección 3.1.1)
    timestamp = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
    monto_str = "{:.2f}".format(monto)

    # Construcción del JSON siguiendo el log exitoso que compartiste
    payload = {
        "OCserviceRequest": {
            "header": {
                "messageFunction": "AUTQ",
                "protocolVersion": "2.0",
                "exchangeIdentification": exchange_id,
                "creationDateTime": timestamp,
                "initiatingParty": {
                    "type": "PID",
                    "identification": REGISTER_ID,
                    "authenticationKey": AUTH_KEY
                },
                "recipientParty": {
                    "type": "TID",
                    "identification": TERMINAL_ID
                }
            },
            "serviceRequest": {
                "serviceContent": "FSPQ",
                "environment": {
                    "POI": {"identification": TERMINAL_ID},
                    "merchant": {"identification": MERCHANT_ID}
                },
                "paymentRequest": {
                    "transactionType": "CRDP",
                    "transactionDetails": {
                        "totalAmount": monto_str,
                        "detailedAmount": {"amountGoodsAndServices": monto_str}
                    }
                }
            }
        }
    }

    print(f"🚀 Enviando petición de ${monto_str} a la nube...")
    
    try:
        # Petición HTTPS POST (Puerto 443)
        response = requests.post(
            URL_CLOUDSVR, 
            json=payload, 
            timeout=80, 
            headers={'Content-Type': 'application/json'}
        )

        if response.status_code == 200:
            data = response.json()
            print("✅ Respuesta recibida de la nube.")
            
            # Intentar leer si fue aprobado
            try:
                # Ruta del JSON según página 94 del manual
                status = data['OCserviceResponse']['serviceResponse']['paymentResponse'][0]['retailerPaymentResult']['authorisationResult']['responseToAuthorisation']['response']
                if status == "APPR":
                    print(f"💰 ¡TRANSACCIÓN APROBADA! (AuthCode: {data['OCserviceResponse']['serviceResponse']['paymentResponse'][0]['retailerPaymentResult']['authorisationResult']['authorisationCode']})")
                else:
                    print(f"❌ Transacción Rechazada: {status}")
            except KeyError:
                print("⚠️ No se encontró el campo de respuesta. Revisa el JSON completo:")
                print(json.dumps(data, indent=2))
        else:
            print(f"❌ Error HTTP {response.status_code}")
            print(response.text)

    except Exception as e:
        print(f"❌ Error de conexión: {str(e)}")

if __name__ == "__main__":
    test_pago_nuvei(1.50) # Prueba con un monto pequeño