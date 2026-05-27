# -*- coding: utf-8 -*-
"""
Clase helper para la comunicación con Nuvei OMNI Channel via REST API.

NO es un modelo de Odoo. Es una clase Python pura que encapsula
las peticiones HTTPS al servidor Nuvei Cloud y construye los
mensajes JSON según el protocolo ISO20022 v2.51.

IMPORTANTE — Hallazgos validados con POSSim + curl contra sandbox real:
  1. La API REST NO usa wrappers ("OCserviceRequest", "OCsessionManagementRequest").
     Se envían directamente "header" + "serviceRequest" (o "sessionManagementRequest")
     como claves raíz del JSON. El servidor devuelve 400 si se usa wrapper.
  2. El terminal (POSSim) se conecta via SSL raw socket (18080) y SÍ usa wrappers.
     El POS (Odoo) se conecta via REST HTTPS (443) y NO usa wrappers.
  3. URL correcta: https://terminal-sandbox.nuvei.com/omnichannel-cloudserver
     (NO usar terminal-poi-sandbox.nuvei.com que tiene 443 cerrado)
  4. El endpoint /session devuelve HTTP 201 (no 200).
  5. STCK (Status Check) NO funciona via REST (null reference error).
     Para validar credenciales se usa NOTI que devuelve APPR si son correctas.

Endpoints RESTful:
  - POST /services → header + serviceRequest (pagos, void, cierre lote)
  - POST /session  → header + sessionManagementRequest (polling, cancel, heartbeat)
  - POST /report   → header + reportRequest (reportes)

Flujo de comunicación - Retail Mode (Sección 2.3.3):
  1. POS envía header + serviceRequest (AUTQ) via POST /services → Nuvei Cloud
  2. Nuvei Cloud responde header + sessionManagementResponse (INPR = en proceso)
  3. POS hace polling con SASQ (RETR) via POST /session → Nuvei Cloud
  4. Nuvei Cloud responde SASP + transactionInProcess mientras terminal procesa
  5. Cuando terminal completa → POS recibe header + serviceResponse (AUTP)
"""
import json
import logging
import time
import uuid
from datetime import datetime, timezone

import pytz
import requests

_logger = logging.getLogger(__name__)

# Versión del protocolo usada en los payloads JSON.
# Todos los ejemplos oficiales del documento (Sección 8) usan "2.0".
NUVEI_PROTOCOL_VERSION = '2.0'

# Timeout por defecto para peticiones HTTP (segundos)
NUVEI_DEFAULT_TIMEOUT = 90

# URLs de Nuvei Cloud (descubiertas con curl, validadas con POSSim)
NUVEI_SANDBOX_URL = 'https://terminal-sandbox.nuvei.com/omnichannel-cloudserver'
NUVEI_PRODUCTION_URL = 'https://terminal.nuvei.com/omnichannel-cloudserver'


class NuveiPosRequest:
    """
    Encapsula la comunicación HTTPS con Nuvei Cloud Service.
    Cada instancia usa las credenciales del payment_method de Odoo.

    Patrón: Igual a razorpay_pos_request.py de Odoo 19.
    """

    def __init__(self, payment_method):
        """
        Inicializa con las credenciales del método de pago configurado en Odoo.

        :param payment_method: registro pos.payment.method con campos nuvei_*
        """
        # Credenciales del POS (Register / Caja Registradora)
        self.device_id = payment_method.nuvei_device_id          # PID - ID del POS/Register
        self.auth_key = payment_method.nuvei_authentication_key  # Auth Key del POS
        # Credenciales del Terminal Nuvei
        self.terminal_id = payment_method.nuvei_terminal_id      # TID - ID del terminal
        # Datos del comercio
        self.merchant_id = payment_method.nuvei_merchant_id      # Merchant ID de Nuvei
        # URL base del servidor Nuvei Cloud (sin trailing slash)
        # Si nuvei_test_mode está activo → sandbox, si no → producción
        # Si el usuario especificó una URL manual, se respeta.
        self.test_mode = payment_method.nuvei_test_mode
        custom_url = (payment_method.nuvei_api_url or '').rstrip('/')
        default_url = NUVEI_SANDBOX_URL if self.test_mode else NUVEI_PRODUCTION_URL
        self.base_url = custom_url or default_url
        # Timeout para peticiones HTTP
        self.timeout = payment_method.nuvei_timeout or NUVEI_DEFAULT_TIMEOUT

    # -------------------------------------------------------------------------
    # MÉTODOS PRIVADOS DE COMUNICACIÓN HTTP
    # -------------------------------------------------------------------------

    def _post(self, endpoint, payload):
        """
        Envía un POST HTTPS al servidor Nuvei Cloud.

        El endpoint /services devuelve HTTP 200.
        El endpoint /session devuelve HTTP 201.
        Ambos son válidos.

        :param endpoint: ruta del endpoint (ej: '/services', '/session')
        :param payload: dict con el JSON a enviar (SIN wrappers OC*)
        :return: dict con la respuesta JSON parseada
        :raises requests.RequestException: si la petición falla
        """
        url = f'{self.base_url}{endpoint}'
        payload_json = json.dumps(payload, indent=2)
        _logger.info(
            '\n╔══ NUVEI REQUEST ══════════════════════════════════════════╗'
            '\n║ POST %s'
            '\n║ Tamaño: %s bytes'
            '\n╠══════════════════════════════════════════════════════════╣'
            '\n%s'
            '\n╚══════════════════════════════════════════════════════════╝',
            url, len(payload_json), payload_json,
        )

        response = requests.post(
            url,
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=self.timeout,
        )

        response_text = response.text[:2000]  # Limitar a 2KB para no saturar logs
        _logger.info(
            '\n╔══ NUVEI RESPONSE ═════════════════════════════════════════╗'
            '\n║ HTTP %s  Content-Type: %s'
            '\n║ Tamaño: %s bytes'
            '\n╠══════════════════════════════════════════════════════════╣'
            '\n%s'
            '\n╚══════════════════════════════════════════════════════════╝',
            response.status_code,
            response.headers.get('Content-Type', '?'),
            len(response.content),
            response_text,
        )

        # /services devuelve 200, /session devuelve 201 — ambos son válidos
        if response.status_code not in (200, 201):
            _logger.error('NUVEI HTTP ERROR: status=%s body=%s', response.status_code, response_text)
            response.raise_for_status()
        return response.json()

    # -------------------------------------------------------------------------
    # CONSTRUCCIÓN DE HEADERS (Sección 3.1.1 del spec)
    # -------------------------------------------------------------------------

    def _build_header(self, message_function, exchange_id=None):
        """
        Construye el header JSON para cualquier mensaje Nuvei.

        El header va directamente en la raíz del JSON (SIN wrapper OC*).
        initiatingParty = POS (PID), recipientParty = Terminal (TID).

        :param message_function: código (AUTQ, RFNQ, RVSL, RCLQ, SASQ, etc.)
        :param exchange_id: UUID para rastrear la transacción (genera uno si no se pasa)
        :return: dict con la estructura del header según spec
        """
        if not exchange_id:
            exchange_id = str(uuid.uuid4())

        return {
            'messageFunction': message_function,
            'protocolVersion': NUVEI_PROTOCOL_VERSION,
            'exchangeIdentification': exchange_id,
            # Zona horaria: Colombia - America/Bogota (UTC-5)
            # Zona horaria: Canadá - America/Toronto (UTC-4)
            'creationDateTime': datetime.now(pytz.timezone('America/Toronto')).strftime(
                '%Y-%m-%dT%H:%M:%S.%f'
            )[:-3] + 'Z',
            'initiatingParty': {
                'identification': self.device_id,
                'type': 'PID',
                'shortName': 'Cash Register ID',
                'authenticationKey': self.auth_key,
            },
            'recipientParty': {
                'identification': self.terminal_id,
                'type': 'TID',
                'shortName': 'Terminal ID',
            },
        }

    # -------------------------------------------------------------------------
    # MÉTODOS PÚBLICOS - LLAMADOS DESDE pos.payment.method
    # -------------------------------------------------------------------------

    def send_payment_request(self, amount, transaction_type='CRDP',
                             invoice_number='', cashier_id='', exchange_id=None,
                             original_transaction_id=''):
        """
        Envía solicitud de pago al terminal Nuvei via POST /services.

        REST: SIN wrapper OCserviceRequest — envía header + serviceRequest directamente.
        La respuesta inicial es sessionManagementResponse con response=INPR
        (indica que la transacción fue aceptada y está en proceso en el terminal).
        Después se debe hacer polling con check_transaction_status() para obtener
        la respuesta final (serviceResponse con AUTP).

        :param amount: monto del pago (ej: 10.00)
        :param transaction_type: tipo (CRDP=venta, RFND=reembolso, RQPA=pre-auth, CMPN=completion)
        :param invoice_number: número de factura (opcional, max 35 chars)
        :param cashier_id: ID del cajero (opcional)
        :param exchange_id: UUID para rastrear la transacción
        :param original_transaction_id: Para refunds (RFND), referencia de la transacción original
        :return: dict con {success, exchange_id, response} o {error, message}
        """
        if not exchange_id:
            exchange_id = str(uuid.uuid4())

        msg_function = self._get_request_message_function(transaction_type)

        _logger.info(
            '\n┌── NUVEI: Enviando pago ──────────────────────────────────┐'
            '\n│ exchange_id:     %s'
            '\n│ messageFunction: %s'
            '\n│ amount:          $%s'
            '\n│ type:            %s'
            '\n│ invoice:         %s'
            '\n│ cashier:         %s'
            '\n│ terminal:        %s → merchant: %s'
            '\n└──────────────────────────────────────────────────────────┘',
            exchange_id, msg_function, f'{amount:.2f}', transaction_type,
            invoice_number or '(vacío)', cashier_id or '(vacío)',
            self.terminal_id, self.merchant_id,
        )
        # Payload SIN wrapper — header + serviceRequest en raíz del JSON
        payload = {
            'header': self._build_header(msg_function, exchange_id),
            'serviceRequest': {
                'environment': {
                    'merchant': {'identification': self.merchant_id},
                    'POI': {'identification': self.terminal_id},
                },
                'context': {
                    'saleContext': {
                        'cashierIdentification': cashier_id or '',
                        'invoiceNumber': invoice_number or '',
                        'identificationType': '',
                    },
                },
                'serviceContent': 'FSRQ' if transaction_type == 'RFND' else 'FSPQ',  # FSRQ=refund, FSPQ=payment
            },
        }

        # Agregar paymentRequest según el tipo de transacción
        # NOTA: Nuvei requiere siempre usar 'paymentRequest' como clave,
        # incluso para refunds (RFND). El tipo se discrimina con transactionType.
        # Ref: Nuvei Support — "change refundRequest to paymentRequest"
        if transaction_type == 'RFND':
            refund_request = {
                'transactionType': 'RFND',
                'transactionDetails': {
                    'totalAmount': f'{amount:.2f}',
                    'detailedAmount': {
                        'amountGoodsAndServices': f'{amount:.2f}',
                    },
                },
            }
            payload['serviceRequest']['paymentRequest'] = refund_request
        else:
            payload['serviceRequest']['paymentRequest'] = {
                # transactionType debe coincidir con el tipo real (CRDP=venta, RQPA=preauth, etc)
                # messageFunction del header también diferencia (AUTQ, FAUQ, etc)
                'transactionType': transaction_type,
                'transactionDetails': {
                    'totalAmount': f'{amount:.2f}',
                    # MOTOIndicator=true → terminal prompts for manual card entry (KEYD)
                    # MOTOIndicator=false → standard card-present flow (chip/tap/swipe)
                    'MOTOIndicator': False,
                    'detailedAmount': {
                        'amountGoodsAndServices': f'{amount:.2f}',
                    },
                },
            }

        try:
            # POST /services — respuesta: header + sessionManagementResponse
            response = self._post('/services', payload)

            # Validar que recibimos respuesta reconocida (sin wrapper)
            if any(key in response for key in (
                'sessionManagementResponse', 'serviceResponse'
            )):
                # Extraer el status de la respuesta inicial
                # Normalmente devuelve sessionManagementResponse con response=INPR o APPR
                mgmt = response.get('sessionManagementResponse', {})
                session_resp = mgmt.get('sessionResponse', {}).get('response', '')

                # ✓ VALIDACIÓN BUSY: Dispositivo ocupado en respuesta inicial
                # Si el terminal responde con BUSY, significa que está procesando otra solicitud
                # El cliente debería reintentar después de esperar
                if session_resp == 'BUSY':
                    _logger.warning(
                        '\n┌── NUVEI: DISPOSITIVO OCUPADO EN AUTQ (BUSY) ──────────┐'
                        '\n│ exchange_id: %s'
                        '\n│ amount: $%s'
                        '\n│ El terminal está ocupado procesando otra solicitud'
                        '\n│ Cliente debe reintentar esta solicitud después'
                        '\n└──────────────────────────────────────────────────────────┘',
                        exchange_id, f'{amount:.2f}',
                    )
                    return {
                        'success': False,
                        'busy': True,  # Flag para diferenciarlo de otros errores
                        'exchange_id': exchange_id,
                        'status': 'BUSY',
                        'message': 'Terminal is busy processing another request - please retry',
                        'response': response,
                    }

                # ✓ IMPORTANTE: Usar el exchange_id ORIGINAL que se envió,
                # NO el del transactionInProcess que retorna el terminal
                # El que retorna el terminal es un ID diferente para tracking interno
                _logger.info(
                    '\n┌── NUVEI: Respuesta inicial del pago ────────────────────┐'
                    '\n│ exchange_id: %s'
                    '\n│ status:      %s'
                    '\n│ (APPR/INPR = Solicitud aceptada, iniciar polling RETR)'
                    '\n└──────────────────────────────────────────────────────────┘',
                    exchange_id, session_resp,
                )
                return {
                    'success': True,
                    'exchange_id': exchange_id,  # ← Usar el original, no el del transactionInProcess
                    'original_exchange_id': exchange_id,
                    'completed': False,  # La transacción apenas se envió
                    'status': session_resp,  # INPR = en proceso en el terminal
                    'response': response,
                }
            return {
                'error': True,
                'message': 'Unexpected server response',
                'response': response,
            }
        except requests.exceptions.Timeout as e:
            _logger.error(
                '\n╔══ NUVEI ERROR: TIMEOUT en POST /services ═════════════╗'
                '\n║ amount: $%s'
                '\n║ transaction_type: %s'
                '\n║ timeout: %s segundos'
                '\n║ IMPORTANTE: No se pudo enviar la transacción al terminal'
                '\n╚═══════════════════════════════════════════════════════╝',
                amount, transaction_type, self.timeout,
            )
            return {
                'error': True,
                'message': f'Timeout sending payment request ({self.timeout}s)',
                'timeout': True,
            }
        except requests.exceptions.ConnectionError as e:
            _logger.error(
                '\n╔══ NUVEI ERROR: CONEXIÓN en POST /services ════════════╗'
                '\n║ amount: $%s'
                '\n║ error: %s'
                '\n║ No se pudo conectar con Nuvei Cloud'
                '\n╚═══════════════════════════════════════════════════════╝',
                amount, str(e),
            )
            return {
                'error': True,
                'message': 'Connection error - Nuvei Cloud cannot be reached',
                'connection_error': True,
            }
        except requests.exceptions.RequestException as e:
            _logger.error('Connection error with Nuvei POST /services: %s', str(e))
            return {'error': True, 'message': f'Connection error: {str(e)}'}

    def check_transaction_status(self, exchange_id):
        """
        Consulta el estado de una transacción via POST /session con RETR.

        REST: SIN wrapper — envía header + sessionManagementRequest directamente.
        Respuestas posibles (comprobado con POSSim + sandbox real):

          1. sessionManagementResponse con response=NOTF
             → Aún no hay respuesta del terminal. Seguir polling.

          2. sessionManagementResponse con transactionInProcess.transactionStatus=ACPT
             → El terminal recibió la orden, sigue procesando. Seguir polling.

          3. serviceResponse con paymentResponse[0].retailerPaymentResult
             → ¡Transacción completada! Contiene auth_code, tarjeta, recibos.

          4. header.messageFunction=TXCN
             → Cancelación desde el terminal.

        :param exchange_id: UUID de la transacción original (del payment_request)
        :return: dict con {success, completed, status, transaction_data} o {error, message}
        """
        # ✓ CRÍTICO: Generar session_id de forma determinística basado en exchange_id
        # Así, para la MISMA transacción, siempre se usa el MISMO session_id en todos los RETR.
        # Nuvei requiere consistencia en el session_id para asociar correctamente los polls.
        session_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, exchange_id))

        _logger.info(
            '\n┌── NUVEI: Polling RETR ──────────────────────────────────┐'
            '\n│ exchange_id original: %s'
            '\n│ session_id polling:   %s'
            '\n└──────────────────────────────────────────────────────────┘',
            exchange_id, session_id,
        )

        # Payload SIN wrapper — header + sessionManagementRequest en raíz del JSON
        # IMPORTANTE: El RETR request debe incluir contexto del ambiente para que Nuvei
        # pueda asociar la consulta con la transacción original. Esto es especialmente
        # crítico en sandbox donde las sesiones pueden ser efímeras.
        payload = {
            'header': self._build_header('SASQ', session_id),
            'sessionManagementRequest': {
                'environment': {
                    'merchant': {'identification': self.merchant_id},
                    'POI': {'identification': self.terminal_id},
                },
                'context': {
                    'saleContext': {
                        'cashierIdentification': '',
                        'invoiceNumber': '',
                        'identificationType': '',
                    },
                },
                'POSComponent': {
                    'POSGroupIdentification': {
                        'exchangeAction': 'RETR',  # Retrieve - consultar estado
                        'exchangeIdentification': exchange_id,
                    },
                },
            },
        }

        try:
            # POST /session — respuesta: header + sessionManagementResponse o serviceResponse
            response = self._post('/session', payload)

            # Analizar respuesta para determinar el estado
            result = {'success': True, 'response': response}

            if 'serviceResponse' in response:
                # Validar que paymentResponse no esté vacío
                # Si está vacío = transacción cancelada desde la terminal
                service_resp = response['serviceResponse']
                payment_responses = service_resp.get('paymentResponse', [])

                if not payment_responses:
                    # ❌ paymentResponse está vacío → Cancelación desde terminal
                    _logger.warning(
                        '\n┌── NUVEI: RESPUESTA VACÍA (paymentResponse=[]) ─────────┐'
                        '\n│ exchange_id: %s'
                        '\n│ ¡TRANSACCIÓN CANCELADA DESDE LA TERMINAL!'
                        '\n│ Se recibió AUTP pero sin datos de pago'
                        '\n└──────────────────────────────────────────────────────────┘',
                        exchange_id,
                    )
                    result['completed'] = True
                    result['cancelled'] = True
                    result['status'] = 'CANCELLED'
                    result['success'] = False
                    result['approved'] = False
                    result['message'] = 'Transaction cancelled by terminal'
                    return result

                # ✓ paymentResponse contiene datos → Transacción completada con éxito
                _logger.info(
                    '\n┌── NUVEI: ¡RESPUESTA FINAL (AUTP)! ─────────────────────┐'
                    '\n│ exchange_id: %s'
                    '\n│ Contiene serviceResponse — parseando datos del pago...'
                    '\n└──────────────────────────────────────────────────────────┘',
                    exchange_id,
                )
                result['completed'] = True
                self._parse_autp_response(service_resp, result)

            elif 'sessionManagementResponse' in response:
                mgmt_resp = response['sessionManagementResponse']

                # Verificar cancelación desde el terminal (TXCN o TXNC)
                # Puede venir en header.messageFunction o en POSGroupIdentification.exchangeType
                msg_fn = response.get('header', {}).get('messageFunction', '')
                pos_component = mgmt_resp.get('POSComponent', {})
                pos_group_id = pos_component.get('POSGroupIdentification', {})
                exchange_type = pos_group_id.get('exchangeType', '')

                if msg_fn in ('TXCN', 'TXNC') or exchange_type in ('TXCN', 'TXNC'):
                    _logger.warning(
                        'NUVEI: Transacción CANCELADA desde el terminal — exchange_id=%s (exchangeType=%s)',
                        exchange_id, exchange_type,
                    )
                    result['completed'] = True
                    result['cancelled'] = True
                    result['status'] = exchange_type or msg_fn or 'TXNC'
                    return result

                # ✓ VALIDACIÓN BUSY: Dispositivo ocupado — debe reintentar después
                # ISO20022 CS-Response: BUSY = "Device is busy"
                # Esto NO es un error, solo indica que el terminal está procesando otra solicitud
                session_code = mgmt_resp.get('sessionResponse', {}).get('response', '')
                if session_code == 'BUSY':
                    _logger.info(
                        '\n┌── NUVEI: DISPOSITIVO OCUPADO (BUSY) ──────────────────┐'
                        '\n│ exchange_id: %s'
                        '\n│ El terminal está procesando otra solicitud'
                        '\n│ El sistema reintentará en la siguiente iteración de polling'
                        '\n└──────────────────────────────────────────────────────────┘',
                        exchange_id,
                    )
                    result['completed'] = False
                    result['status'] = 'BUSY'
                    result['busy'] = True  # Flag para diferenciarlo de NOTF
                    return result

                # Verificar si hay transacción en proceso
                txn_in_process = mgmt_resp.get('transactionInProcess', {})
                if txn_in_process:
                    result['completed'] = False
                    result['status'] = txn_in_process.get('transactionStatus', '')
                    _logger.info(
                        'NUVEI RETR: En proceso — txStatus=%s exchangeId=%s',
                        result['status'],
                        txn_in_process.get('exchangeIdentification', ''),
                    )
                else:
                    # Solo respuesta de sesión, sin info de transacción
                    result['completed'] = False
                    result['status'] = session_code
                    _logger.info(
                        'NUVEI RETR: Solo sesión — response=%s (NOTF=sin respuesta aún)',
                        session_code,
                    )
            else:
                result['completed'] = False
                result['status'] = 'UNKNOWN'
                _logger.warning('NUVEI RETR: Respuesta sin claves conocidas — keys=%s', list(response.keys()))

            return result

        except requests.exceptions.Timeout as e:
            # Timeout de conexión — transacción puede estar atascada en el terminal
            _logger.error(
                '\n╔══ NUVEI ERROR: TIMEOUT ═══════════════════════════════╗'
                '\n║ exchange_id: %s'
                '\n║ endpoint: POST /session'
                '\n║ timeout: %s segundos'
                '\n║ IMPORTANTE: La transacción puede estar en proceso en el terminal'
                '\n║ Solución: Esperar 60s y reintentar, o usar terminal directamente'
                '\n╚═══════════════════════════════════════════════════════╝',
                exchange_id, self.timeout,
            )
            return {
                'error': True,
                'message': f'Connection timeout ({self.timeout}s) - The transaction may still be processing on the terminal',
                'timeout': True,
                'exchange_id': exchange_id,
            }
        except requests.exceptions.ConnectionError as e:
            _logger.error(
                '\n╔══ NUVEI ERROR: CONEXIÓN ══════════════════════════════╗'
                '\n║ exchange_id: %s'
                '\n║ endpoint: POST /session'
                '\n║ error: %s'
                '\n║ La conexión se perdió durante el polling'
                '\n╚═══════════════════════════════════════════════════════╝',
                exchange_id, str(e),
            )
            return {
                'error': True,
                'message': 'Connection error - The transaction may still be processing on the terminal',
                'connection_error': True,
                'exchange_id': exchange_id,
            }
        except requests.exceptions.RequestException as e:
            _logger.error('Error consultando estado Nuvei POST /session: %s', str(e))
            return {'error': True, 'message': f'Connection error: {str(e)}'}

    def _parse_autp_response(self, service_response, result):
        """
        Extrae los datos del pago de la serviceResponse (AUTP) del terminal.

        Estructura real de la respuesta AUTP (confirmado con POSSim):
          serviceResponse.paymentResponse[0]:
            .saleTransactionIdentification.transactionReference  → referencia de venta
            .POITransactionIdentification.transactionReference    → referencia del terminal
            .retailerPaymentResult:
              .transactionType → CRDP
              .transactionResponse:
                .authorisationResult.responseToAuthorisation.response → APPR/DCLN
                .authorisationResult.authorisationCode → auth code (ej: A15973)
                .receiptDetails:
                  .mskPan → tarjeta enmascarada (ej: ************8656)
                  .cardLbl → tipo de tarjeta (ej: CREDIT/JCB)
                  .cardAID → AID de la tarjeta
                  .apprdeclISO → texto aprobación (ej: APPROVED 000)
                  .hostInvoice → invoice del host
                  .hostSequence → secuencia del host
              .transactionDetails.totalAmount → monto cobrado
            .receipt[] → array de recibos (merchant + customer copies)

        :param service_response: dict de la serviceResponse (sin wrapper)
        :param result: dict donde guardar los datos (se modifica in-place)
        """
        # Buscar el primer paymentResponse (puede ser una lista)
        payment_responses = service_response.get('paymentResponse', [])
        if not payment_responses:
            result['status'] = 'UNKNOWN'
            return

        # Puede ser lista o dict directo
        if isinstance(payment_responses, list):
            pay_resp = payment_responses[0]
        else:
            pay_resp = payment_responses

        # Extraer resultado del terminal
        retail_result = pay_resp.get('retailerPaymentResult', {})
        tx_response = retail_result.get('transactionResponse', {})
        auth_result = tx_response.get('authorisationResult', {})
        receipt_details = tx_response.get('receiptDetails', {})

        # Código de aprobación/rechazo
        # Buscar en múltiples ubicaciones (diferentes versiones del spec ISO20022):
        #   - Nivel superior (spec v2.46): serviceResponse.response.responseCode ← PRINCIPAL
        #   - Profundo (POSSim/legacy): paymentResponse[0].retailerPaymentResult.transactionResponse.authorisationResult.responseToAuthorisation.responseCode
        #   - Alternativo: responseToAuthorisation.response (legacy)

        response_code = ''

        # 1️⃣ PRIMERA OPCIÓN: Top-level serviceResponse.response.responseCode (SPEC v2.46+)
        response_code = service_response.get('response', {}).get('responseCode', '')

        # 2️⃣ FALLBACK: Profundo en paymentResponse
        if not response_code:
            resp_to_auth = auth_result.get('responseToAuthorisation', {})
            response_code = resp_to_auth.get('responseCode', '') or resp_to_auth.get('response', '')

        result['status'] = response_code

        # Validar si fue aprobado o rechazado
        # APPR = Approved, DECL/DCLN = Declined
        is_approved = response_code in ['APPR', 'APPROVED']
        result['success'] = is_approved
        result['approved'] = is_approved

        # Construir transaction_data para el frontend JS
        result['transaction_data'] = {
            # Código de autorización (ej: A15973)
            'auth_code': auth_result.get('authorisationCode', ''),
            # Tarjeta enmascarada (ej: ************8656)
            'card_number': receipt_details.get('mskPan', ''),
            # Tipo de tarjeta (ej: CREDIT/JCB)
            'card_type': receipt_details.get('cardLbl', ''),
            # Tipo de cuenta (VISA, MC, AMEX, DISC, etc.)
            'account_type': receipt_details.get('accountType', ''),
            # AID de la tarjeta EMV
            'card_aid': receipt_details.get('cardAID', ''),
            # Modo de entrada (C = chip, S = swipe, T = tap)
            'entry_mode': receipt_details.get('cardDataNtryMd', ''),
            # Texto de aprobación/rechazo ISO (ej: APPROVED 000)
            'approval_text': receipt_details.get('apprdeclISO', ''),
            # Referencia de venta del POS
            'sale_reference': pay_resp.get(
                'saleTransactionIdentification', {}
            ).get('transactionReference', ''),
            # Referencia del terminal
            'terminal_reference': pay_resp.get(
                'POITransactionIdentification', {}
            ).get('transactionReference', ''),
            # Transaction ID (usamos la referencia del terminal como ID principal)
            'transaction_id': pay_resp.get(
                'POITransactionIdentification', {}
            ).get('transactionReference', ''),
            # Referencia del host
            'host_invoice': receipt_details.get('hostInvoice', ''),
            'host_sequence': receipt_details.get('hostSequence', ''),
            # Monto cobrado (está bajo transactionResponse.transactionDetails)
            'total_amount': tx_response.get(
                'transactionDetails', {}
            ).get('totalAmount', ''),
            # Tipo de transacción
            'transaction_type': retail_result.get('transactionType', ''),
            # Exchange ID de la transacción
            'exchange_id': '',  # Se llena desde el header
        }

        # Exchange ID del header de la respuesta
        header = {}
        # El header puede estar a nivel raíz (ya lo tenemos en response del caller)
        # Pero aquí solo tenemos serviceResponse, asi que lo dejamos vacío
        # El JS ya tiene el exchange_id del request original

        # Extraer recibos
        receipts = pay_resp.get('receipt', [])
        if receipts:
            result['transaction_data']['receipts'] = []
            for receipt in receipts:
                result['transaction_data']['receipts'].append({
                    'type': receipt.get('documentQualifier', ''),
                    'content': receipt.get('outputContent', ''),
                })

        td = result['transaction_data']
        _logger.info(
            '\n╔══ NUVEI: PAGO COMPLETADO ═════════════════════════════════╗'
            '\n║ Status:       %s'
            '\n║ Auth Code:    %s'
            '\n║ Tarjeta:      %s'
            '\n║ Tipo:         %s'
            '\n║ Entrada:      %s'
            '\n║ Aprobación:   %s'
            '\n║ Terminal Ref: %s'
            '\n║ Sale Ref:     %s'
            '\n║ Host Invoice: %s'
            '\n║ Monto:        $%s'
            '\n║ Recibos:      %s'
            '\n╚══════════════════════════════════════════════════════════╝',
            response_code,
            td['auth_code'],
            td['card_number'],
            td['card_type'],
            td['entry_mode'],
            td['approval_text'],
            td['terminal_reference'],
            td['sale_reference'],
            td['host_invoice'],
            td['total_amount'],
            f"{len(receipts)} recibo(s)" if receipts else 'ninguno',
        )

    def cancel_transaction(self, exchange_id):
        """
        Cancela una transacción en proceso via POST /session con CUCL/FCXL.

        REST: SIN wrapper — envía header + sessionManagementRequest directamente.

        :param exchange_id: UUID de la transacción a cancelar
        :return: dict con {success, response} o {error, message}
        """
        # ✓ CRÍTICO: Generar session_id de forma determinística basado en exchange_id
        # Así, para la MISMA transacción, siempre se usa el MISMO session_id.
        session_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, exchange_id))

        _logger.info(
            '\n┌── NUVEI: Cancelando transacción ──────────────────────────┐'
            '\n│ exchange_id: %s'
            '\n│ action: CUCL (Force Cancel / FCXL)'
            '\n└──────────────────────────────────────────────────────────┘',
            exchange_id,
        )

        # Payload SIN wrapper — header + sessionManagementRequest en raíz
        # exchangeType fue removido en spec v2.46; FCXL va como exchangeAction
        payload = {
            'header': self._build_header('SASQ', session_id),
            'sessionManagementRequest': {
                'environment': {
                    'merchant': {'identification': self.merchant_id},
                    'POI': {'identification': self.terminal_id},
                },
                'context': {
                    'saleContext': {
                        'cashierIdentification': '',
                        'invoiceNumber': '',
                        'identificationType': '',
                    },
                },
                'POSComponent': {
                    'POSGroupIdentification': {
                        'exchangeAction': 'FCXL',  # Force Cancel (spec v2.06+)
                        'exchangeIdentification': exchange_id,
                    },
                },
            },
        }

        try:
            response = self._post('/session', payload)
            if response:
                _logger.info('NUVEI: Cancelación enviada OK — exchange_id=%s', exchange_id)
                return {'success': True, 'response': response}
            _logger.warning('NUVEI: Sin respuesta al cancelar — exchange_id=%s', exchange_id)
            return {'error': True, 'message': 'No response while cancelling'}
        except requests.exceptions.RequestException as e:
            _logger.error('Error cancelling Nuvei transaction POST /session: %s', str(e))
            return {'error': True, 'message': f'Connection error: {str(e)}'}

    def send_void_request(self, original_transaction_id, transaction_type='CRDP',
                          invoice_number='', cashier_id=''):
        """
        Envía solicitud de anulación/reversa via POST /services con RVSL.

        REST: SIN wrapper — envía header + serviceRequest directamente.

        :param original_transaction_id: referencia de la transacción original
        :param transaction_type: tipo de la transacción original
        :param invoice_number: número de factura original
        :param cashier_id: ID del cajero
        :return: dict con resultado
        """
        exchange_id = str(uuid.uuid4())

        _logger.info(
            '\n┌── NUVEI: Enviando VOID (FMPV) ────────────────────────────┐'
            '\n│ original_transaction_id: %s'
            '\n│ exchange_id (nuevo):     %s'
            '\n│ transaction_type:        %s'
            '\n└──────────────────────────────────────────────────────────┘',
            original_transaction_id, exchange_id, transaction_type,
        )

        # Void/Reversal per spec Sección 3.2.1 y sample 6.8
        # messageFunction = FMPV (reversalRequest with financial capture)
        # Incluye AMBOS paymentRequest + reversalRequest como en el sample oficial
        payload = {
            'header': self._build_header('FMPV', exchange_id),
            'serviceRequest': {
                'environment': {
                    'merchant': {'identification': self.merchant_id},
                    'POI': {'identification': self.terminal_id},
                },
                'context': {
                    'saleContext': {
                        'cashierIdentification': cashier_id or '',
                        'invoiceNumber': invoice_number or '',
                        'identificationType': '',
                    },
                },
                'serviceContent': 'FSPQ',
                'paymentRequest': {
                    'transactionType': transaction_type,
                    'transactionIdentification': original_transaction_id,
                    'transactionDetails': {
                        'totalAmount': '0.00',
                    },
                },
                'reversalRequest': {
                    'reversalTransaction': {
                        'transactionType': transaction_type,
                        'transactionIdentification': original_transaction_id,
                    },
                },
            },
        }

        try:
            response = self._post('/services', payload)
            if response:
                _logger.info('NUVEI: VOID enviado OK — exchange_id=%s', exchange_id)
                result = {
                    'success': True,
                    'exchange_id': exchange_id,
                    'response': response,
                }
                # Parsear datos de la respuesta si viene serviceResponse (AUTP)
                if 'serviceResponse' in response:
                    self._parse_autp_response(response['serviceResponse'], result)
                return result
            _logger.warning('NUVEI: Sin respuesta del terminal para VOID')
            return {'error': True, 'message': 'No terminal response'}
        except requests.exceptions.RequestException as e:
            _logger.error('Error enviando void a Nuvei POST /services: %s', str(e))
            return {'error': True, 'message': f'Connection error: {str(e)}'}

    def send_refund_request(self, original_transaction_id, amount, transaction_type='CRDP',
                            invoice_number='', cashier_id=''):
        """
        Envía solicitud de reembolso parcial via POST /services con RFNQ.

        Para reembolsos parciales (ej: $1.05 de una transacción de $100).

        REST: SIN wrapper — envía header + serviceRequest directamente.

        :param original_transaction_id: referencia de la transacción original
        :param amount: monto a reembolsar (ej: 1.05)
        :param transaction_type: tipo de la transacción original (CRDP, DCRD, etc.)
        :param invoice_number: número de factura original
        :param cashier_id: ID del cajero
        :return: dict con resultado
        """
        exchange_id = str(uuid.uuid4())

        _logger.info(
            '\n┌── NUVEI: Enviando REFUND PARCIAL (RFNQ) ──────────────────┐'
            '\n│ original_transaction_id: %s'
            '\n│ exchange_id (nuevo):     %s'
            '\n│ monto a reembolsar:      $%.2f'
            '\n│ transaction_type:        %s'
            '\n└──────────────────────────────────────────────────────────┘',
            original_transaction_id, exchange_id, amount, transaction_type,
        )

        # Refund Request per spec ISO20022 v2.51
        # messageFunction = RFNQ (Refund Request)
        payload = {
            'header': self._build_header('RFNQ', exchange_id),
            'serviceRequest': {
                'environment': {
                    'merchant': {'identification': self.merchant_id},
                    'POI': {'identification': self.terminal_id},
                },
                'context': {
                    'saleContext': {
                        'cashierIdentification': cashier_id or '',
                        'invoiceNumber': invoice_number or '',
                        'identificationType': '',
                    },
                },
                'serviceContent': 'FSRQ',  # Financial Service Request for Refund
                # Nuvei requiere 'paymentRequest' (no 'refundRequest') con transactionType='RFND'.
                # transactionIdentification no es necesario según Nuvei Support.
                # Ref: Nuvei Support — "change refundRequest to paymentRequest"
                'paymentRequest': {
                    'transactionType': 'RFND',
                    'transactionDetails': {
                        'totalAmount': f'{amount:.2f}',
                        'detailedAmount': {
                            'amountGoodsAndServices': f'{amount:.2f}',
                        },
                    },
                },
            },
        }

        try:
            response = self._post('/services', payload)
            if response:
                _logger.info('NUVEI: REFUND PARCIAL enviado OK — exchange_id=%s amount=%.2f', exchange_id, amount)
                result = {
                    'success': True,
                    'exchange_id': exchange_id,
                    'response': response,
                }
                # Parsear datos de la respuesta si viene serviceResponse (AUTP)
                if 'serviceResponse' in response:
                    self._parse_autp_response(response['serviceResponse'], result)
                return result
            _logger.warning('NUVEI: Sin respuesta del terminal para REFUND PARCIAL')
            return {'error': True, 'message': 'No terminal response'}
        except requests.exceptions.RequestException as e:
            _logger.error('Error enviando refund parcial a Nuvei POST /services: %s', str(e))
            return {'error': True, 'message': f'Connection error: {str(e)}'}

    def batch_close(self):
        """
        Envía solicitud de cierre de lote via POST /services con RCLQ.

        REST: SIN wrapper — envía header + serviceRequest directamente.
        El terminal responde con RCLP incluyendo totales del lote.

        Si Nuvei devuelve APPR pero aún está procesando (INPR), hace polling
        automático con backoff hasta obtener RCLP o timeout.

        :return: dict con resultado del cierre {success, exchange_id, response, batch_data, status}
        """
        exchange_id = str(uuid.uuid4())

        # Payload SIN wrapper — header + serviceRequest en raíz
        payload = {
            'header': self._build_header('RCLQ', exchange_id),
            'serviceRequest': {
                'environment': {
                    'merchant': {'identification': self.merchant_id},
                    'POI': {'identification': self.terminal_id},
                },
                'context': {
                    'saleContext': {
                        'cashierIdentification': '',
                        'invoiceNumber': '',
                        'identificationType': '',
                    },
                },
                'serviceContent': 'FSPQ',
                'batchRequest': {
                    'removeAllFlag': '1',
                },
            },
        }

        try:
            response = self._post('/services', payload)
            if response:
                result = {
                    'success': True,
                    'exchange_id': exchange_id,
                    'response': response,
                }
                # Parsear datos del cierre de lote si viene serviceResponse
                if 'serviceResponse' in response:
                    self._parse_batch_close_response(response['serviceResponse'], result)
                    return result

                # Algunos entornos devuelven solo sessionManagementResponse (sin totales)
                if 'sessionManagementResponse' in response:
                    session_resp = response.get('sessionManagementResponse', {})
                    status = session_resp.get('sessionResponse', {}).get('response')
                    if status:
                        result['status'] = status

                    # Si quedó APPR o INPR/INIT, hacer RETR hasta obtener RCLP completo
                    if status in ('APPR', 'INPR', 'INIT'):
                        # Aumentar reintentos si el estado es APPR (más probable que necesite polling)
                        max_attempts = 8 if status == 'APPR' else 6
                        retr_result = self._poll_retr(exchange_id, max_attempts=max_attempts, initial_delay=0.5)
                        result['retrieved_response'] = retr_result.get('response')

                        if retr_result.get('service_response'):
                            self._parse_batch_close_response(
                                retr_result['service_response'],
                                result,
                            )
                        elif retr_result.get('status'):
                            result['status'] = retr_result['status']

                        if retr_result.get('error'):
                            result['retrieval_error'] = retr_result.get('message')

                return result
            return {'error': True, 'message': 'No response during batch close'}
        except requests.exceptions.RequestException as e:
            _logger.error('Error en batch close Nuvei: %s', str(e))
            return {'error': True, 'message': f'Connection error: {str(e)}'}

    def report_totals(self, report_type='TMTST'):
        """
        Solicita un reporte de totales via POST /report (RPTQ).

        :param report_type: CS-ReportType (ej: TMTST, HSTS)
        :return: dict con resultado del reporte
        """
        exchange_id = str(uuid.uuid4())

        payload = {
            'header': self._build_header('RPTQ', exchange_id),
            'reportRequest': {
                'environment': {
                    'merchant': {'identification': self.merchant_id},
                    'POI': {'identification': self.terminal_id},
                },
                'context': {
                    'saleContext': {
                        'cashierIdentification': '',
                        'invoiceNumber': '',
                        'identificationType': '',
                    },
                },
                'serviceContent': 'FSCQ',
                'reportTransactionRequest': {
                    'reportType': report_type,
                },
            },
        }

        try:
            response = self._post('/report', payload)
            if response:
                result = {
                    'success': True,
                    'exchange_id': exchange_id,
                    'response': response,
                }
                if 'reportResponse' in response:
                    self._parse_report_totals_response(response['reportResponse'], result)
                    return result
                if 'sessionManagementResponse' in response:
                    session_resp = response.get('sessionManagementResponse', {})
                    status = session_resp.get('sessionResponse', {}).get('response')
                    if status:
                        result['status'] = status
                    if status in ('INPR', 'INIT'):
                        retr_result = self._poll_retr(exchange_id)
                        result['retrieved_response'] = retr_result.get('response')
                        if retr_result.get('report_response'):
                            self._parse_report_totals_response(
                                retr_result['report_response'],
                                result,
                            )
                        elif retr_result.get('error'):
                            result.update({
                                'error': True,
                                'message': retr_result.get('message', 'Error en RETR'),
                            })
                return result
            return {'error': True, 'message': 'No report response'}
        except requests.exceptions.RequestException as e:
            _logger.error('Error en report_totals Nuvei: %s', str(e))
            return {'error': True, 'message': f'Connection error: {str(e)}'}

    def _poll_retr(self, exchange_id, max_attempts=6, initial_delay=0.5):
        """
        Hace polling RETR con backoff exponencial para recuperar respuestas.

        Reintentos: 0.5s, 0.75s, 1.13s, 1.69s, 2.54s, 3.81s (máx 6 intentos)
        Total: hasta ~10.5 segundos con delays incluidos.

        :param exchange_id: exchangeIdentification original
        :param max_attempts: número máximo de intentos (default 6)
        :param initial_delay: segundos de espera inicial (default 0.5s)
        :return: dict con {success, response, status, service_response, report_response}
                 o {error, message, response}
        """
        delay = initial_delay

        _logger.info(
            'NUVEI: Iniciando polling RETR para exchange_id=%s '
            '(max_attempts=%d, initial_delay=%.2fs)',
            exchange_id, max_attempts, initial_delay
        )

        for attempt in range(1, max_attempts + 1):
            payload = {
                'header': self._build_header('SASQ', str(uuid.uuid4())),
                'sessionManagementRequest': {
                    'environment': {
                        'merchant': {'identification': self.merchant_id},
                        'POI': {'identification': self.terminal_id},
                    },
                    'context': {
                        'saleContext': {
                            'cashierIdentification': '',
                            'invoiceNumber': '',
                            'identificationType': '',
                        },
                    },
                    'POSComponent': {
                        'POSGroupIdentification': {
                            'exchangeAction': 'RETR',
                            'exchangeIdentification': exchange_id,
                        },
                    },
                },
            }

            try:
                response = self._post('/session', payload)
            except requests.exceptions.RequestException as e:
                _logger.error('Error en RETR Nuvei (intento %d/%d): %s',
                            attempt, max_attempts, str(e))
                return {'error': True, 'message': f'Connection error: {str(e)}'}

            # Prioritizar reportResponse (para RPTQ)
            if 'reportResponse' in response:
                _logger.info('NUVEI: RETR obtuvo reportResponse en intento %d', attempt)
                return {
                    'success': True,
                    'response': response,
                    'report_response': response.get('reportResponse'),
                }

            # Luego serviceResponse (para RCLQ)
            if 'serviceResponse' in response:
                _logger.info('NUVEI: RETR obtuvo serviceResponse en intento %d', attempt)
                return {
                    'success': True,
                    'response': response,
                    'service_response': response.get('serviceResponse'),
                }

            # Finalmente sessionManagementResponse
            if 'sessionManagementResponse' in response:
                session_resp = response.get('sessionManagementResponse', {})
                status = session_resp.get('sessionResponse', {}).get('response')
                transaction_in_process = session_resp.get('transactionInProcess', {})

                # Si hay transactionInProcess, siempre reintentar (a menos que sea el último intento)
                if transaction_in_process and attempt < max_attempts:
                    tx_status = transaction_in_process.get('transactionStatus', '')
                    _logger.debug(
                        'NUVEI: RETR transactionInProcess=%s en intento %d/%d, '
                        'esperando %.2fs...',
                        tx_status, attempt, max_attempts, delay
                    )
                    time.sleep(delay)
                    delay = min(delay * 1.5, 5.0)
                    continue

                # Si no hay transactionInProcess y el status es terminal
                if status not in ('INPR', 'INIT', 'NOTF') or attempt >= max_attempts:
                    _logger.info('NUVEI: RETR terminó con status=%s en intento %d',
                                status, attempt)
                    return {
                        'success': True,
                        'status': status,
                        'response': response,
                    }

                # Reintentar con backoff por INPR/INIT/NOTF
                if attempt < max_attempts:
                    _logger.debug(
                        'NUVEI: RETR obtuvo %s en intento %d/%d, esperando %.2fs...',
                        status, attempt, max_attempts, delay
                    )
                    time.sleep(delay)
                    delay = min(delay * 1.5, 5.0)
                    continue

            # Respuesta no reconocida
            _logger.warning('NUVEI: RETR respuesta sin formato válido en intento %d', attempt)
            return {
                'error': True,
                'message': 'RETR did not return a valid response',
                'response': response,
            }

        _logger.error('NUVEI: RETR agotó reintentos (%d) sin respuesta final', max_attempts)
        return {'error': True, 'message': f'RETR did not return a response after {max_attempts} attempts'}

    def test_connection(self):
        """
        Prueba la conexión y valida credenciales con Nuvei Cloud.

        IMPORTANTE: STCK (Status Check) NO funciona via REST — devuelve un error
        de null reference del servidor .NET. En su lugar usamos NOTI (heartbeat
        desde perspectiva POS) que valida:
          - POS ID (PID) y authenticationKey son válidos
          - Terminal ID (TID) existe y está asociado
          - Responde APPR si todo es correcto

        El endpoint es POST /session con NOTI.
        SIN wrapper — envía header + sessionManagementRequest directamente.

        :return: dict con {success, response, response_code} o {error, message}
        """
        exchange_id = str(uuid.uuid4())

        # Usar NOTI en vez de STCK — STCK no funciona via REST
        payload = {
            'header': self._build_header('SASQ', exchange_id),
            'sessionManagementRequest': {
                'environment': {
                    'merchant': {'identification': self.merchant_id},
                    'POI': {'identification': self.terminal_id},
                },
                'context': {
                    'saleContext': {
                        'cashierIdentification': '',
                        'invoiceNumber': '',
                        'identificationType': '',
                    },
                },
                'POSComponent': {
                    'POSGroupIdentification': {
                        'exchangeAction': 'NOTI',  # Heartbeat — valida credenciales
                    },
                },
            },
        }

        try:
            # POST /session — respuesta: header + sessionManagementResponse
            response = self._post('/session', payload)
            if not response:
                return {'error': True, 'message': 'No server response'}

            result = {'success': True, 'response': response}

            # La respuesta viene SIN wrapper: { header, sessionManagementResponse }
            if 'sessionManagementResponse' in response:
                mgmt_resp = response['sessionManagementResponse']
                resp_code = mgmt_resp.get('sessionResponse', {}).get('response', '')

                result['response_code'] = resp_code

                # Verificar códigos de error de credenciales
                if resp_code == 'INTP':
                    return {
                        'error': True,
                        'message': 'Invalid credentials: incorrect Device ID (PID) or Authentication Key',
                        'response': response,
                    }
                if resp_code == 'RCPP':
                    return {
                        'error': True,
                        'message': 'Terminal ID (TID) not found or invalid',
                        'response': response,
                    }
                if resp_code == 'UNMP':
                    return {
                        'error': True,
                        'message': 'No relationship: this POS is not associated with the terminal',
                        'response': response,
                    }
                if resp_code == 'VERS':
                    return {
                        'error': True,
                        'message': 'Unsupported protocol version',
                        'response': response,
                    }

                # APPR = credenciales válidas, conexión exitosa
                if resp_code == 'APPR':
                    _logger.info(
                        '\n╔══ NUVEI: CONEXIÓN EXITOSA ════════════════════════════════╗'
                        '\n║ Respuesta: APPR — credenciales válidas'
                        '\n║ PID: %s  TID: %s'
                        '\n╚══════════════════════════════════════════════════════════╝',
                        self.device_id, self.terminal_id
                    )
            return result

        except requests.exceptions.RequestException as e:
            _logger.error('Error en test_connection Nuvei: %s', str(e))
            return {'error': True, 'message': f'Connection error: {str(e)}'}

    # -------------------------------------------------------------------------
    # HELPERS INTERNOS
    # -------------------------------------------------------------------------

    def _get_request_message_function(self, transaction_type):
        """
        Mapea el tipo de transacción al messageFunction del request.
        Sección 7.3.8 - CS-MessageFunction.

        :param transaction_type: código de tipo (CRDP, RFND, RQPA, CMPN)
        :return: código messageFunction para el request
        """
        mapping = {
            'CRDP': 'AUTQ',  # Venta → Authorization Request
            'RFND': 'RFNQ',  # Reembolso → Refund Request
            'RQPA': 'FAUQ',  # Pre-autorización → Financial Auth Request (spec 6.10)
            'CMPN': 'CMPV',  # Completar pre-auth → Completion Request
        }
        return mapping.get(transaction_type, 'AUTQ')

    def _parse_batch_close_response(self, service_response, result):
        """
        Extrae los totales del cierre de lote de la serviceResponse (RCLP).

        Estructura esperada del batchResponse:
          serviceResponse.batchResponse:
            .batchTotals:
              .salesCount / .salesAmount
              .refundsCount / .refundsAmount
              .netCount / .netAmount
            .response → APPR/DCLN
            .dataSource.host[] o .dataSource.terminal[] → buckets con Count/Amount

        :param service_response: dict de la serviceResponse
        :param result: dict donde guardar los datos (se modifica in-place)
        """
        batch_resp = service_response.get('batchResponse', {})

        if not batch_resp:
            # Intentar extraer del response genérico
            resp = service_response.get('response', {})
            result['status'] = resp.get('responseCode', '') or resp.get('response', 'UNKNOWN')
            _logger.debug('NUVEI: Sin batchResponse, status=%s', result.get('status'))
            return

        result['status'] = batch_resp.get('response', 'APPR')

        # Intentar extraer batchTotals primero
        totals = batch_resp.get('batchTotals', {})

        if totals:
            result['batch_data'] = {
                'sales_count': int(totals.get('salesCount', 0) or 0),
                'sales_amount': f"{float(totals.get('salesAmount', 0) or 0):.2f}",
                'refunds_count': int(totals.get('refundsCount', 0) or 0),
                'refunds_amount': f"{float(totals.get('refundsAmount', 0) or 0):.2f}",
                'net_count': int(totals.get('netCount', 0) or 0),
                'net_amount': f"{float(totals.get('netAmount', 0) or 0):.2f}",
            }
            _logger.info(
                'NUVEI: Batch totales extraídos - sales:%d, refunds:%d, net:%d',
                result['batch_data']['sales_count'],
                result['batch_data']['refunds_count'],
                result['batch_data']['net_count']
            )
            return

        # Fallback: extraer de dataSource buckets
        def _to_float(value):
            try:
                return float(value or 0)
            except (TypeError, ValueError):
                return 0.0

        data_source = batch_resp.get('dataSource', {})
        buckets = data_source.get('host') or data_source.get('terminal') or []

        if not isinstance(buckets, list):
            buckets = [buckets] if buckets else []

        sales_types = {'CRDT', 'CRDP', 'SALE', 'AUTQ', 'DEBT'}
        refund_types = {'RFND', 'RFDT', 'RFDN', 'REFD', 'REFUND', 'RVSL'}

        sales_count = 0
        sales_amount = 0.0
        refunds_count = 0
        refunds_amount = 0.0

        for item in buckets:
            tx_type = (item.get('TransType') or item.get('transType') or '').upper()
            count = int(item.get('Count') or item.get('count') or 0)
            amount = _to_float(item.get('Amount') or item.get('amount') or 0)

            if tx_type in sales_types:
                sales_count += count
                sales_amount += amount
                _logger.debug('NUVEI: Bucket %s: count=%d, amount=%.2f (sales)', tx_type, count, amount)
            elif tx_type in refund_types:
                refunds_count += count
                refunds_amount += amount
                _logger.debug('NUVEI: Bucket %s: count=%d, amount=%.2f (refund)', tx_type, count, amount)

        result['batch_data'] = {
            'sales_count': sales_count,
            'sales_amount': f'{sales_amount:.2f}',
            'refunds_count': refunds_count,
            'refunds_amount': f'{refunds_amount:.2f}',
            'net_count': sales_count - refunds_count,
            'net_amount': f'{(sales_amount - refunds_amount):.2f}',
        }

        _logger.info(
            'NUVEI: Batch totales de dataSource - sales:%d (%.2f), refunds:%d (%.2f)',
            sales_count, sales_amount, refunds_count, refunds_amount
        )
        bd = result['batch_data']
        _logger.info(
            '\n╔══ NUVEI: CIERRE DE LOTE ══════════════════════════════════╗'
            '\n║ Status:      %s'
            '\n║ Ventas:      %s x $%s'
            '\n║ Reembolsos:  %s x $%s'
            '\n║ Neto:        %s x $%s'
            '\n╚══════════════════════════════════════════════════════════╝',
            result['status'],
            bd['sales_count'], bd['sales_amount'],
            bd['refunds_count'], bd['refunds_amount'],
            bd['net_count'], bd['net_amount'],
        )

    def _parse_report_totals_response(self, report_response, result):
        """
        Extrae totales desde reportResponse.reportGetTotalsResponse.

        :param report_response: dict de reportResponse
        :param result: dict donde guardar los datos (se modifica in-place)
        """
        resp = report_response.get('response', {})
        result['status'] = resp.get('responseCode', '') or resp.get('response', 'UNKNOWN')

        totals_resp = report_response.get('reportGetTotalsResponse', {})
        totals_set = totals_resp.get('transactionTotalsSet', [])

        if isinstance(totals_set, dict):
            totals_set = [totals_set]

        def _to_float(value):
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0

        sales_count = 0
        sales_amount = 0.0
        refunds_count = 0
        refunds_amount = 0.0

        for item in totals_set or []:
            tx_type = (item.get('type') or '').strip()
            count = item.get('totalNumber') or item.get('count') or 0
            amount = item.get('cumulativeAmount') or item.get('amount') or 0

            try:
                count = int(count)
            except (TypeError, ValueError):
                count = 0

            amount = _to_float(amount)

            if tx_type in ('Debit', 'CardPayment', 'CRDP'):
                sales_count += count
                sales_amount += amount
            elif tx_type in ('Credit', 'Refund', 'RFND'):
                refunds_count += count
                refunds_amount += amount

        result['batch_data'] = {
            'sales_count': sales_count,
            'sales_amount': f'{sales_amount:.2f}',
            'refunds_count': refunds_count,
            'refunds_amount': f'{refunds_amount:.2f}',
            'net_count': sales_count - refunds_count,
            'net_amount': f'{(sales_amount - refunds_amount):.2f}',
        }
