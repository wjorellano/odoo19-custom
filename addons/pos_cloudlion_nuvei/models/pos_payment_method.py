# -*- coding: utf-8 -*-
"""
Extensión del modelo pos.payment.method para Nuvei.

Agrega la opción 'nuvei' al selector de terminales de pago y define
los campos de configuración necesarios para conectar con Nuvei Cloud.

Cada método público de este modelo es llamado desde el frontend JS
via ORM call (this.env.services.orm.silent.call), NO via controllers HTTP.
Esto sigue el estándar de Odoo 19 (ver pos_razorpay, pos_adyen, etc.).
"""
import json
import logging
import time
import datetime
import pytz
from odoo import fields, models, api, _
from odoo.exceptions import UserError
from .nuvei_pos_request import NuveiPosRequest, NUVEI_SANDBOX_URL, NUVEI_PRODUCTION_URL

_logger = logging.getLogger(__name__)


class PosPaymentMethod(models.Model):
    _inherit = 'pos.payment.method'


    def _get_payment_terminal_selection(self):
        """Agrega 'Nuvei' a la lista de terminales de pago disponibles en Odoo POS."""
        return super()._get_payment_terminal_selection() + [('nuvei', 'Nuvei')]

    nuvei_device_id = fields.Char(
        string="Device ID (PID)",
        help='Register / POS ID provided by Nuvei. '
             'It is the initiatingParty in transactions (type=PID).',
        copy=False,
    )
    nuvei_terminal_id = fields.Char(
        string="Terminal ID (TID)",
        help='Payment terminal ID provided by Nuvei. '
             'It is the recipientParty in transactions (type=TID).',
        copy=False,
    )
    nuvei_authentication_key = fields.Char(
        string="Authentication Key",
        help='POS device authentication key provided by Nuvei during boarding.',
        copy=False,
        groups='point_of_sale.group_pos_user',
    )
    nuvei_merchant_id = fields.Char(
        string="Merchant ID",
        help='Merchant ID assigned by Nuvei.',
        copy=False,
    )
    nuvei_api_url = fields.Char(
        string="API URL",
        help='Base URL of the Nuvei Cloud server (RESTful HTTPS, port 443).\n'
             'IMPORTANT: include /omnichannel-cloudserver in the URL.\n'
             'Sandbox: https://terminal-sandbox.nuvei.com/omnichannel-cloudserver\n'
             'Production: https://terminal.nuvei.com/omnichannel-cloudserver',
        default='https://terminal-sandbox.nuvei.com/omnichannel-cloudserver',
        copy=False,
    )
    nuvei_test_mode = fields.Boolean(
        string="Test Mode",
        help='Enable sandbox mode for test transactions. '
             'Automatically switches the URL between sandbox and production.',
        default=True,
    )

    @api.onchange('nuvei_test_mode')
    def _onchange_nuvei_test_mode(self):
        if self.use_payment_terminal == 'nuvei':
            if self.nuvei_test_mode:
                self.nuvei_api_url = NUVEI_SANDBOX_URL
            else:
                self.nuvei_api_url = NUVEI_PRODUCTION_URL

    nuvei_timeout = fields.Integer(
        string="Timeout (seconds)",
        help='Maximum wait time for a terminal response. '
             'It should be long enough for the customer to insert the card and enter the PIN.',
        default=120,
    )

    # CONFIGURACIÓN DE BATCH CLOSE

    nuvei_batch_close_manual = fields.Boolean(
        string="Allow Manual Batch Close",
        help='Enables the "Batch Close" button in the configuration view '
             'to close batches manually from Odoo.',
        default=True,
    )
    nuvei_batch_close_auto = fields.Boolean(
        string="Automatic Batch Close",
        help='Enables automatic batch close daily at the time configured in module settings.',
        default=False,
    )
    nuvei_batch_close_last_date = fields.Datetime(
        string="Last Close Date",
        help='Date and time of the last completed batch close (prevents duplicates).',
        readonly=True,
        copy=False,
    )
    nuvei_latest_response = fields.Text(
        string="Latest Response",
        help='Latest response from the Nuvei terminal (debug).',
        copy=False,
        readonly=True,
        groups='base.group_system',
    )

    # CAMPOS ENVIADOS AL FRONTEND (JS)
    # Solo enviamos lo necesario para que el JS pueda mostrar info.

    def _get_pos_ui_payment_method_fields(self, is_config):
        """Expone los campos de configuración al frontend del POS."""
        res = super()._get_pos_ui_payment_method_fields(is_config)
        return res

    # PROTECCIÓN DE ESCRITURA
    # Permite actualizar nuvei_latest_response incluso con sesión POS abierta
    # (necesario para guardar respuestas de debug durante transacciones).

    def _is_write_forbidden(self, fields):
        """Excluye nuvei_latest_response de la protección de escritura."""
        return super()._is_write_forbidden(fields - {'nuvei_latest_response'})


    def nuvei_make_payment_request(self, data):
        """
        Inicia un pago en el terminal Nuvei.

        Llamado desde: PaymentNuvei.sendPaymentRequest() en el JS.
        Conecta con: Nuvei Cloud via HTTPS POST /services.
        Protocolo: header + serviceRequest con messageFunction AUTQ (SIN wrapper OC*).

        :param data: dict con {amount, transaction_type, invoice_number, cashier_id, exchange_id, original_transaction_id}
        :return: dict con {success, exchange_id, response} o {error, message}
        """
        self.ensure_one()
        _logger.info(
            '[NUVEI ORM] nuvei_make_payment_request — amount=$%s type=%s invoice=%s',
            data.get('amount', 0), data.get('transaction_type', 'CRDP'),
            data.get('invoice_number', '')
        )
        nuvei = NuveiPosRequest(self)
        result = nuvei.send_payment_request(
            amount=data.get('amount', 0),
            transaction_type=data.get('transaction_type', 'CRDP'),
            invoice_number=data.get('invoice_number', ''),
            cashier_id=data.get('cashier_id', ''),
            exchange_id=data.get('exchange_id'),
            original_transaction_id=data.get('original_transaction_id', ''),
        )
        # Guardar respuesta completa para debug (solo backend, no se envía al JS)
        if result.get('response'):
            self.sudo().nuvei_latest_response = json.dumps(result['response'], indent=2)
        _logger.info(
            '[NUVEI ORM] nuvei_make_payment_request — resultado: success=%s exchange_id=%s status=%s',
            result.get('success'), result.get('exchange_id'), result.get('status', result.get('message', '')),
        )
        _logger.debug(
            '[NUVEI ORM] nuvei_make_payment_request — respuesta completa:\n%s',
            json.dumps(result, indent=2, default=str),
        )
        # Enviar solo los campos necesarios al JS (SIN raw response por seguridad)
        return {
            'success': result.get('success', False),
            'exchange_id': result.get('exchange_id', ''),
            'completed': result.get('completed', False),
            'status': result.get('status', ''),
            'error': result.get('error', False),
            'message': result.get('message', ''),
            'timeout': self.nuvei_timeout or 120,
        }

    def nuvei_fetch_payment_status(self, data):
        """
        Consulta el estado de una transacción en proceso.

        Llamado desde: PaymentNuvei._waitForPaymentConfirmation() en el JS (polling).
        Conecta con: Nuvei Cloud via Session Management.
        Protocolo: header + sessionManagementRequest con SASQ + exchangeAction=RETR (SIN wrapper OC*).

        :param data: dict con {exchange_id}
        :return: dict con {success, response} o {error, message}
        """
        self.ensure_one()
        _logger.info(
            '[NUVEI ORM] nuvei_fetch_payment_status (RETR) — exchange_id=%s',
            data.get('exchange_id'),
        )
        nuvei = NuveiPosRequest(self)
        result = nuvei.check_transaction_status(
            exchange_id=data.get('exchange_id'),
        )
        if result.get('response'):
            self.sudo().nuvei_latest_response = json.dumps(result['response'], indent=2)
        _logger.info(
            '[NUVEI ORM] nuvei_fetch_payment_status — completed=%s status=%s',
            result.get('completed'), result.get('status', result.get('message', '')),
        )
        _logger.debug(
            '[NUVEI ORM] nuvei_fetch_payment_status — respuesta completa:\n%s',
            json.dumps(result, indent=2, default=str),
        )

        # Si hay error de timeout o conexión, registrarlo especialmente
        if result.get('timeout') or result.get('connection_error'):
            _logger.warning(
                '[NUVEI ORM] TIMEOUT/CONEXIÓN en nuvei_fetch_payment_status\n'
                'exchange_id: %s\n'
                'error: %s\n'
                'ACCIÓN RECOMENDADA: Esperar 60s y reintentar con nuvei_retry_fetch_payment_status',
                data.get('exchange_id'), result.get('message', 'Unknown error'),
            )

        # Enviar solo los campos necesarios al JS (SIN raw response por seguridad)
        return {
            'success': result.get('success', False),
            'completed': result.get('completed', False),
            'cancelled': result.get('cancelled', False),
            'status': result.get('status', ''),
            'transaction_data': result.get('transaction_data'),
            'error': result.get('error', False),
            'message': result.get('message', ''),
        }

    def nuvei_retry_fetch_payment_status(self, data):
        """
        Reintenta consultar el estado después de un timeout.

        Llamado desde el JS cuando nuvei_fetch_payment_status falla con timeout/conexión.
        Espera 2s antes de reintentar para permitir que el terminal se estabilice.

        :param data: dict con {exchange_id}
        :return: dict igual a nuvei_fetch_payment_status
        """
        import time

        self.ensure_one()
        exchange_id = data.get('exchange_id')

        _logger.warning(
            '[NUVEI ORM] nuvei_retry_fetch_payment_status — Reintentando después de timeout\n'
            'exchange_id: %s\n'
            'esperando 2 segundos para estabilización del terminal...',
            exchange_id,
        )

        # Esperar 2 segundos para que el terminal se estabilice
        time.sleep(2)

        # Reintentar consulta
        _logger.info('[NUVEI ORM] nuvei_retry_fetch_payment_status — reintentando RETR...')
        nuvei = NuveiPosRequest(self)
        result = nuvei.check_transaction_status(exchange_id=exchange_id)

        if result.get('response'):
            self.sudo().nuvei_latest_response = json.dumps(result['response'], indent=2)

        _logger.info(
            '[NUVEI ORM] nuvei_retry_fetch_payment_status — completed=%s status=%s',
            result.get('completed'), result.get('status', result.get('message', '')),
        )
        _logger.debug(
            '[NUVEI ORM] nuvei_retry_fetch_payment_status — respuesta completa:\n%s',
            json.dumps(result, indent=2, default=str),
        )

        # Si vuelve a fallar, loguear especialmente
        if result.get('timeout') or result.get('connection_error'):
            _logger.error(
                '[NUVEI ORM] REINTENTOS AGOTADOS en nuvei_retry_fetch_payment_status\n'
                'exchange_id: %s\n'
                'error: %s\n'
                'RECOMENDACIÓN: Verifica conexión de red, reinicia el terminal o contacta soporte',
                exchange_id, result.get('message', 'Unknown error'),
            )

        return {
            'success': result.get('success', False),
            'completed': result.get('completed', False),
            'cancelled': result.get('cancelled', False),
            'status': result.get('status', ''),
            'transaction_data': result.get('transaction_data'),
            'error': result.get('error', False),
            'message': result.get('message', ''),
            'retry': True,  # Bandera para indicar que esto fue un reintentos
        }

    def nuvei_cancel_payment(self, data):
        """
        Cancela una transacción en proceso.

        Llamado desde: PaymentNuvei.sendPaymentCancel() en el JS.
        Puede ser:
          - Manual: usuario presiona "Cancelar"
          - Automático: frontend detecró timeout de 120s

        Protocolo: header + sessionManagementRequest con exchangeAction=CUCL + FCXL.

        :param data: dict con {exchange_id, auto_cancel} (auto_cancel es opcional)
        :return: dict con {success, response} o {error, message}
        """
        self.ensure_one()
        exchange_id = data.get('exchange_id')
        is_auto_cancel = data.get('auto_cancel', False)

        cancel_reason = "CANCELACIÓN AUTOMÁTICA POR TIMEOUT (120s)" if is_auto_cancel else "Manual"

        _logger.warning(
            '[NUVEI ORM] nuvei_cancel_payment — %s\n'
            'exchange_id: %s',
            cancel_reason, exchange_id,
        )
        nuvei = NuveiPosRequest(self)
        result = nuvei.cancel_transaction(
            exchange_id=exchange_id,
        )
        if result.get('response'):
            self.sudo().nuvei_latest_response = json.dumps(result['response'], indent=2)
        _logger.info('[NUVEI ORM] nuvei_cancel_payment — resultado: %s', 'OK' if result.get('success') else result.get('message'))
        _logger.debug(
            '[NUVEI ORM] nuvei_cancel_payment — respuesta completa:\n%s',
            json.dumps(result, indent=2, default=str),
        )
        # Enviar solo resultado limpio al JS
        return {
            'success': result.get('success', False),
            'error': result.get('error', False),
            'message': result.get('message', ''),
        }

    def nuvei_make_refund_request(self, data):
        """
        Envía solicitud de reembolso/anulación de una transacción ya completada.

        Llamado desde: PaymentNuvei.sendPaymentReversal() en el JS.
        Protocolo: header + serviceRequest con messageFunction RFNQ o FMPV.

        :param data: dict con {
            original_transaction_id,
            transaction_type,
            amount (para refund parcial),
            invoice_number,
            cashier_id
        }
        :return: dict con resultado
        """
        self.ensure_one()

        refund_amount = float(data.get('amount', 0.0) or 0.0)
        original_tx_id = data.get('original_transaction_id')
        tx_type = data.get('transaction_type', 'CRDP')

        _logger.info(
            '[NUVEI ORM] nuvei_make_refund_request — original_tx=%s type=%s amount=$%.2f',
            original_tx_id, tx_type, refund_amount,
        )

        nuvei = NuveiPosRequest(self)

        # Si hay monto específico > 0: es un refund PARCIAL (RFNQ)
        # Si no hay monto o es 0: es una anulación COMPLETA (FMPV/void)
        if refund_amount > 0:
            _logger.info('[NUVEI ORM] ← Usando RFNQ (Refund parcial) por $%.2f', refund_amount)
            result = nuvei.send_refund_request(
                original_transaction_id=original_tx_id,
                amount=refund_amount,
                transaction_type=tx_type,
                invoice_number=data.get('invoice_number', ''),
                cashier_id=data.get('cashier_id', ''),
            )
        else:
            _logger.info('[NUVEI ORM] ← Usando FMPV (Anulación completa/void)')
            result = nuvei.send_void_request(
                original_transaction_id=original_tx_id,
                transaction_type=tx_type,
                invoice_number=data.get('invoice_number', ''),
                cashier_id=data.get('cashier_id', ''),
            )

        if result.get('response'):
            self.sudo().nuvei_latest_response = json.dumps(result['response'], indent=2)

        _logger.info('[NUVEI ORM] nuvei_make_refund_request — resultado: %s', 'OK' if result.get('success') else result.get('message'))
        _logger.debug(
            '[NUVEI ORM] nuvei_make_refund_request — respuesta completa:\n%s',
            json.dumps(result, indent=2, default=str),
        )

        # Enviar solo resultado limpio al JS
        return {
            'success': result.get('success', False),
            'exchange_id': result.get('exchange_id', ''),
            'transaction_data': result.get('transaction_data'),
            'error': result.get('error', False),
            'message': result.get('message', ''),
        }

    def nuvei_make_preauth_request(self, data):
        """
        Inicia una pre-autorización en el terminal Nuvei.

        Llamado desde el frontend JS para retener fondos sin captura inmediata.
        Protocolo: header + serviceRequest con messageFunction FAUQ.

        :param data: dict con {amount, invoice_number, cashier_id, exchange_id}
        :return: dict con {success, exchange_id, status} o {error, message}
        """
        self.ensure_one()
        _logger.info(
            '[NUVEI ORM] nuvei_make_preauth_request — amount=$%s invoice=%s',
            data.get('amount', 0), data.get('invoice_number', ''),
        )
        nuvei = NuveiPosRequest(self)
        result = nuvei.send_payment_request(
            amount=data.get('amount', 0),
            transaction_type='RQPA',
            invoice_number=data.get('invoice_number', ''),
            cashier_id=data.get('cashier_id', ''),
            exchange_id=data.get('exchange_id'),
        )
        if result.get('response'):
            self.sudo().nuvei_latest_response = json.dumps(result['response'], indent=2)
        _logger.info(
            '[NUVEI ORM] nuvei_make_preauth_request — resultado: success=%s exchange_id=%s',
            result.get('success'), result.get('exchange_id'),
        )
        _logger.debug(
            '[NUVEI ORM] nuvei_make_preauth_request — respuesta completa:\n%s',
            json.dumps(result, indent=2, default=str),
        )
        return {
            'success': result.get('success', False),
            'exchange_id': result.get('exchange_id', ''),
            'completed': result.get('completed', False),
            'status': result.get('status', ''),
            'error': result.get('error', False),
            'message': result.get('message', ''),
            'timeout': self.nuvei_timeout or 120,
        }

    def nuvei_make_completion_request(self, data):
        """
        Completa (captura) una pre-autorización previamente aprobada.

        Protocolo: header + serviceRequest con messageFunction CMPV.

        :param data: dict con {amount, original_exchange_id, invoice_number, cashier_id}
        :return: dict con {success, exchange_id} o {error, message}
        """
        self.ensure_one()
        _logger.info(
            '[NUVEI ORM] nuvei_make_completion_request — amount=$%s original=%s',
            data.get('amount', 0), data.get('original_exchange_id', ''),
        )
        nuvei = NuveiPosRequest(self)
        result = nuvei.send_payment_request(
            amount=data.get('amount', 0),
            transaction_type='CMPN',
            invoice_number=data.get('invoice_number', ''),
            cashier_id=data.get('cashier_id', ''),
            exchange_id=data.get('exchange_id'),
        )
        if result.get('response'):
            self.sudo().nuvei_latest_response = json.dumps(result['response'], indent=2)
        _logger.info(
            '[NUVEI ORM] nuvei_make_completion_request — resultado: success=%s',
            result.get('success'),
        )
        _logger.debug(
            '[NUVEI ORM] nuvei_make_completion_request — respuesta completa:\n%s',
            json.dumps(result, indent=2, default=str),
        )
        return {
            'success': result.get('success', False),
            'exchange_id': result.get('exchange_id', ''),
            'completed': result.get('completed', False),
            'status': result.get('status', ''),
            'error': result.get('error', False),
            'message': result.get('message', ''),
            'timeout': self.nuvei_timeout or 120,
        }

    # -------------------------------------------------------------------------
    # BOTÓN TEST CONNECTION (llamado desde la vista XML)
    # -------------------------------------------------------------------------

    def test_nuvei_connection(self):
        """
        Verifica credenciales y estado del terminal Nuvei.

        Llamado desde: botón "Probar Conexión" en la vista de configuración.
        Conecta con: Nuvei Cloud via HTTPS POST /session.
        Protocolo: header + sessionManagementRequest con SASQ + exchangeAction=NOTI.

        NOTI (heartbeat POS) valida:
          - POS ID y Authentication Key (si inválidos → INTP)
          - Terminal ID existe (si inválido → RCPP)
          - Asociación POS↔Terminal (si no existe → UNMP)
          - Responde APPR si todo es correcto

        Nota: STCK (Status Check) NO funciona via REST (devuelve null reference).

        :return: action de notificación con resultado
        """
        self.ensure_one()
        if not all([self.nuvei_device_id, self.nuvei_terminal_id,
                     self.nuvei_authentication_key, self.nuvei_merchant_id]):
            raise UserError(_('Missing Nuvei configuration fields'))

        _logger.info(
            '[NUVEI ORM] test_nuvei_connection — PID=%s TID=%s URL=%s',
            self.nuvei_device_id, self.nuvei_terminal_id, self.nuvei_api_url,
        )
        nuvei = NuveiPosRequest(self)
        result = nuvei.test_connection()

        if result.get('response'):
            self.sudo().nuvei_latest_response = json.dumps(result['response'], indent=2)

        _logger.debug(
            '[NUVEI ORM] test_nuvei_connection — respuesta completa:\n%s',
            json.dumps(result, indent=2, default=str),
        )

        if result.get('success'):
            # Construir mensaje con info de la conexión
            resp_code = result.get('response_code', '')
            pending = result.get('pending_exchange', '')
            parts = [
                _('Valid credentials!'),
                _('Terminal: %(tid)s', tid=self.nuvei_terminal_id),
                _('Response: %(resp)s', resp=resp_code),
            ]
            if pending:
                parts.append(_('Pending transaction: %(eid)s', eid=pending[:12] + '...'))
            msg = '\n'.join(str(p) for p in parts)
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Nuvei Connection'),
                    'message': msg,
                    'type': 'success',
                    'sticky': False,
                },
            }
        raise UserError(_(
            'Verification failed: %(msg)s',
            msg=result.get('message', 'Unknown error'),
        ))

    # -------------------------------------------------------------------------
    # BOTONES DE PRUEBA (para testing con simulador)
    # -------------------------------------------------------------------------

    def action_test_void(self):
        """Anula la última transacción exitosa del terminal."""
        self.ensure_one()

        # Buscar última transacción con exchange_id
        last_payment = self.env['pos.payment'].search([
            ('payment_method_id', '=', self.id),
            ('nuvei_exchange_id', '!=', False)
        ], order='id desc', limit=1)

        if not last_payment:
            raise UserError(_('No previous transactions to void'))

        _logger.info(
            '[NUVEI TEST] Anulando transacción: exchange_id=%s monto=$%s',
            last_payment.nuvei_exchange_id, last_payment.amount
        )

        # Llamar directamente a NuveiPosRequest
        nuvei = NuveiPosRequest(self)
        result = nuvei.cancel_transaction(exchange_id=last_payment.nuvei_exchange_id)

        _logger.debug(
            '[NUVEI TEST] Anulación de transacción — respuesta completa:\n%s',
            json.dumps(result, indent=2, default=str),
        )

        if result.get('success'):
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Void Transaction'),
                    'message': _('Transaction voided successfully\nExchange ID: %(eid)s',
                                 eid=last_payment.nuvei_exchange_id[:16] + '...'),
                    'type': 'success',
                    'sticky': True,
                }
            }
        else:
            raise UserError(_(
                'Error voiding transaction: %(msg)s',
                msg=result.get('message', 'Unknown error')
            ))

    def action_test_refund(self):
        """Procesa un reembolso de prueba de $1.00 USD."""
        self.ensure_one()

        # Generar invoice único con timestamp
        invoice_no = f'TEST-REFUND-{int(time.time())}'

        _logger.info('[NUVEI TEST] Procesando refund de prueba: $1.00 invoice=%s', invoice_no)

        # Llamar directamente a NuveiPosRequest con transaction_type='RFND'
        nuvei = NuveiPosRequest(self)
        result = nuvei.send_payment_request(
            amount=1.00,
            transaction_type='RFND',  # Esto se mapea a RFNQ
            invoice_number=invoice_no,
            cashier_id='ADMIN'
        )

        _logger.debug(
            '[NUVEI TEST] Envío de refund — respuesta completa:\n%s',
            json.dumps(result, indent=2, default=str),
        )

        if not result.get('success'):
            raise UserError(_(
                'Error sending refund: %(msg)s',
                msg=result.get('message', 'Unknown error')
            ))

        exchange_id = result.get('exchange_id', '')

        # Hacer polling para obtener resultado final
        _logger.info('[NUVEI TEST] Esperando respuesta del terminal...')
        time.sleep(2)

        if exchange_id:
            status_result = nuvei.check_transaction_status(exchange_id)

            _logger.debug(
                '[NUVEI TEST] Consulta de estado refund — respuesta completa:\n%s',
                json.dumps(status_result, indent=2, default=str),
            )

            if status_result.get('completed'):
                tx_data = status_result.get('transaction_data', {})
                msg_parts = [
                    _('Refund completed'),
                    _('Amount: $1.00 USD'),
                    _('Invoice: %(inv)s', inv=invoice_no),
                    _('Auth Code: %(auth)s', auth=tx_data.get('auth_code', 'N/A')),
                    _('Card: %(card)s', card=tx_data.get('card_number', 'N/A')),
                ]
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Test Refund'),
                        'message': '\n'.join(msg_parts),
                        'type': 'success',
                        'sticky': True,
                    }
                }
            else:
                raise UserError(_(
                    'Refund not completed: %(status)s',
                    status=status_result.get('status', 'TIMEOUT')
                ))
        else:
            raise UserError(_('No exchange_id was returned for the refund'))

    def action_test_batch_close(self):
        """Ejecuta un cierre de lote (batch settlement)."""
        self.ensure_one()

        _logger.info('[NUVEI TEST] Ejecutando cierre de lote...')

        result = self._nuvei_batch_close_and_report()

        _logger.debug(
            '[NUVEI TEST] Cierre de lote — respuesta completa:\n%s',
            json.dumps(result, indent=2, default=str),
        )

        if result.get('success'):
            status = result.get('status', 'UNKNOWN')
            batch_data = result.get('batch_data', {})

            msg_parts = [
                _('Batch close completed'),
                _('Status: %(status)s', status=status),
            ]
            if batch_data:
                msg_parts.append(_('Sales: %(count)s x $%(amount)s',
                                   count=batch_data.get('sales_count', 0),
                                   amount=batch_data.get('sales_amount', '0.00')))
                msg_parts.append(_('Refunds: %(count)s x $%(amount)s',
                                   count=batch_data.get('refunds_count', 0),
                                   amount=batch_data.get('refunds_amount', '0.00')))
                msg_parts.append(_('Net: %(count)s x $%(amount)s',
                                   count=batch_data.get('net_count', 0),
                                   amount=batch_data.get('net_amount', '0.00')))

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Batch Close'),
                    'message': '\n'.join(msg_parts),
                    'type': 'success',
                    'sticky': True,
                }
            }
        else:
            raise UserError(_(
                'Batch close error: %(msg)s',
                msg=result.get('message', 'Unknown error')
            ))

    def _nuvei_batch_close_and_report(self, raise_on_error=True):
        """
        Ejecuta batch close y guarda el reporte en nuvei.batch.close.report.

        Flujo:
        1. Envía RCLQ a /services
        2. Si Nuvei devuelve APPR/INPR, hace polling RETR hasta obtener RCLP
        3. Extrae batch_data de Nuvei si está disponible
        4. Si no, calcula totales desde pos.payment (fallback)
        5. Crea registro en nuvei.batch.close.report con totales
        6. Retorna resultado {success, message, status}
        """
        self.ensure_one()

        _logger.info(
            'NUVEI: Iniciando batch close y reporte para payment_method_id=%d (%s)',
            self.id, self.name
        )

        # Llamar directamente a NuveiPosRequest
        nuvei = NuveiPosRequest(self)
        result = nuvei.batch_close()

        if not result.get('success'):
            msg = result.get('message', 'Unknown error')
            _logger.error('NUVEI: Batch close falló: %s', msg)
            if raise_on_error:
                raise UserError(_('Batch close error: %(msg)s', msg=msg))
            return result

        batch_data = result.get('batch_data', {})
        raw_response = {
            'batch_close': result.get('response', {}),
            'exchange_id': result.get('exchange_id'),
            'status': result.get('status'),
        }

        # Si vino respuesta recuperada (RETR), registrar también
        if result.get('retrieved_response'):
            raw_response['retrieved_response'] = result['retrieved_response']

        if result.get('retrieval_error'):
            raw_response['retrieval_error'] = result['retrieval_error']

        def _to_float(value):
            """Convierte valor seguro a float."""
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0

        # Si Nuvei no devolvió batch_data, usar totales de Odoo como fallback
        if not batch_data:
            _logger.info(
                'NUVEI: batch_data vacío, usando fallback de totales de Odoo'
            )

            sales_count = 0
            sales_amount = 0.0
            refunds_count = 0
            refunds_amount = 0.0

            # Buscar último reporte para filtrar pagos posteriores
            last_report = self.env['nuvei.batch.close.report'].sudo().search([
                ('payment_method_id', '=', self.id)
            ], order='date desc', limit=1)

            # ✓ 1. Contar pagos POS (con sesión abierta)
            payment_model = self.env['pos.payment']
            pos_domain = [('payment_method_id', '=', self.id)]

            if 'payment_status' in payment_model._fields:
                pos_domain.append(('payment_status', '=', 'done'))

            if last_report and last_report.date:
                pos_domain.append(('payment_date', '>', last_report.date))

            pos_payments = payment_model.sudo().search(pos_domain)

            for payment in pos_payments:
                amount = payment.amount or 0.0
                if amount >= 0:
                    sales_count += 1
                    sales_amount += amount
                else:
                    refunds_count += 1
                    refunds_amount += abs(amount)

            # ✓ 2. Contar pagos de invoices SIN sesión (nuvei_exchange_id presente)
            invoice_domain = [
                ('nuvei_payment_method_id', '=', self.id),
                ('nuvei_exchange_id', '!=', False),
                ('state', '=', 'posted'),
            ]

            if last_report and last_report.date:
                invoice_domain.append(('nuvei_payment_date', '>', last_report.date))

            invoices = self.env['account.move'].sudo().search(invoice_domain)

            for invoice in invoices:
                # Usar el monto pagado (puede ser pago parcial)
                amount = invoice.amount_residual * -1  # Negativo porque es un pago
                if amount >= 0:
                    sales_count += 1
                    sales_amount += amount
                else:
                    refunds_count += 1
                    refunds_amount += abs(amount)

            _logger.info(
                'NUVEI: Totales de Odoo (POS=%d, Invoices=%d) - sales:%d (%.2f), refunds:%d (%.2f)',
                len(pos_payments), len(invoices), sales_count, sales_amount, refunds_count, refunds_amount
            )

            batch_data = {
                'sales_count': sales_count,
                'sales_amount': f'{sales_amount:.2f}',
                'refunds_count': refunds_count,
                'refunds_amount': f'{refunds_amount:.2f}',
                'net_count': sales_count - refunds_count,
                'net_amount': f'{(sales_amount - refunds_amount):.2f}',
            }
            raw_response['odoo_totals'] = batch_data

            _logger.info(
                'NUVEI: Batch totales de Odoo - sales:%d (%.2f), refunds:%d (%.2f)',
                sales_count, sales_amount, refunds_count, refunds_amount
            )

        # Crear registro en nuvei.batch.close.report
        try:
            report = self.env['nuvei.batch.close.report'].sudo().create({
                'date': fields.Datetime.now(),
                'payment_method_id': self.id,
                'status': result.get('status', 'COMPLETED'),
                'sales_count': batch_data.get('sales_count', 0),
                'sales_amount': _to_float(batch_data.get('sales_amount')),
                'refunds_count': batch_data.get('refunds_count', 0),
                'refunds_amount': _to_float(batch_data.get('refunds_amount')),
                'net_count': batch_data.get('net_count', 0),
                'net_amount': _to_float(batch_data.get('net_amount')),
                'raw_response': json.dumps(raw_response, indent=2, default=str),
            })

            _logger.info(
                'NUVEI: Reporte de cierre creado - id=%d, sales=%d, refunds=%d',
                report.id,
                report.sales_count,
                report.refunds_count
            )

            return {
                'success': True,
                'status': result.get('status'),
                'message': f'Batch closed: {batch_data.get("sales_count", 0)} sales, '
                          f'{batch_data.get("refunds_count", 0)} refunds',
                'report_id': report.id,
            }

        except Exception as e:
            _logger.error('Error creando reporte de batch close: %s', str(e))
            if raise_on_error:
                raise UserError(_('Error saving close report: %(msg)s', msg=str(e)))
            return {
                'error': True,
                'message': f'Error saving report: {str(e)}',
            }
    def action_manual_batch_close(self):
        """Ejecuta batch close manualmente desde la interfaz de configuración."""
        if not self.nuvei_batch_close_manual:
            raise UserError(_('Manual batch close is not enabled for this payment method'))

        _logger.info('NUVEI: Iniciando batch close manual para payment_method_id=%d', self.id)

        try:
            result = self._nuvei_batch_close_and_report(raise_on_error=True)

            # Actualizar last_date
            self.nuvei_batch_close_last_date = fields.Datetime.now()

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Manual Batch Close'),
                    'message': result.get('message', 'Batch close completed'),
                    'type': 'success',
                    'sticky': True,
                }
            }
        except UserError as e:
            _logger.error('Error en batch close manual: %s', str(e))
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Batch Close Error'),
                    'message': str(e),
                    'type': 'danger',
                    'sticky': True,
                }
            }

    def _run_batch_close_auto_if_needed(self):
        try:
            # Obtener configuración global del módulo
            config = self.env['nuvei.module.config'].get_config()

            if not config.batch_close_enabled:
                _logger.debug('NUVEI BATCH CLOSE: Deshabilitado globalmente')
                return

            # Buscar todos los payment methods con auto batch close habilitado
            payment_methods = self.env['pos.payment.method'].search([
                ('nuvei_batch_close_auto', '=', True),
                ('use_payment_terminal', '=', 'nuvei'),
            ])

            if not payment_methods:
                _logger.debug('NUVEI BATCH CLOSE: No hay payment methods con auto batch close')
                return

            _logger.warning(
                'NUVEI BATCH CLOSE: ★ Iniciando batch close diario para %d payment methods',
                len(payment_methods)
            )

            # tz_name = 'America/Bogota'  # Colombia timezone (UTC-5)
            tz_name = 'America/Toronto'  # Canada timezone (UTC-4)
            now_utc = fields.Datetime.now()
            local_now = fields.Datetime.context_timestamp(
                self.env.user.with_context(tz=tz_name), now_utc
            )
            local_today = local_now.date()

            success_count = 0
            error_count = 0

            for pm in payment_methods:
                # Verificar que no se haya ejecutado hoy (evitar duplicados)
                last_close_date = pm.nuvei_batch_close_last_date
                if last_close_date:
                    last_close_local = fields.Datetime.context_timestamp(
                        self.env.user.with_context(tz=tz_name), last_close_date
                    )
                    if last_close_local.date() == local_today:
                        _logger.info(
                            'NUVEI BATCH CLOSE: PM "%s" (ID:%d) - Ya ejecutado hoy (%s), omitiendo',
                            pm.name, pm.id, last_close_local.strftime('%Y-%m-%d %H:%M:%S')
                        )
                        continue

                # Ejecutar batch close automático
                try:
                    _logger.info(
                        'NUVEI BATCH CLOSE: Ejecutando para PM "%s" (ID:%d)',
                        pm.name, pm.id
                    )

                    result = pm._nuvei_batch_close_and_report(raise_on_error=True)

                    # Actualizar fecha de último cierre
                    pm.nuvei_batch_close_last_date = fields.Datetime.now()
                    success_count += 1

                    _logger.warning(
                        'NUVEI BATCH CLOSE: ✓ EXITOSO - PM "%s" (ID:%d) - '
                        'Resultado: %s | Report ID: %d',
                        pm.name, pm.id, result.get('message', 'Batch closed'),
                        result.get('report_id', 0)
                    )

                except Exception as e:
                    error_count += 1
                    _logger.error(
                        'NUVEI BATCH CLOSE: ✗ ERROR en PM "%s" (ID:%d): %s',
                        pm.name, pm.id, str(e), exc_info=True
                    )

            _logger.warning(
                'NUVEI BATCH CLOSE: ★ Completado - Exitosos: %d, Errores: %d',
                success_count, error_count
            )

        except Exception as e:
            _logger.error(
                'NUVEI BATCH CLOSE: ✗ EXCEPCIÓN no capturada: %s',
                str(e), exc_info=True
            )
