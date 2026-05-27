# -*- coding: utf-8 -*-
"""Wizard para pagar facturas con terminal Nuvei y registrar en POS."""
import time

from odoo import api, fields, models, _, Command
from odoo.exceptions import UserError

from .nuvei_pos_request import NuveiPosRequest


class NuveiInvoicePaymentWizard(models.TransientModel):
    _name = 'nuvei.invoice.payment.wizard'
    _description = 'Nuvei Invoice Terminal Payment'

    invoice_id = fields.Many2one(
        'account.move',
        string='Invoice',
        required=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        'res.currency',
        related='invoice_id.currency_id',
        readonly=True,
    )
    amount = fields.Monetary(
        string='Amount to Pay',
        currency_field='currency_id',
        required=True,
    )
    payment_method_id = fields.Many2one(
        'pos.payment.method',
        string='Payment Method',
        required=True,
        domain="[('use_payment_terminal', '=', 'nuvei')]",
    )
    pos_config_id = fields.Many2one(
        'pos.config',
        string='POS Configuration',
        required=True,
    )
    pos_product_id = fields.Many2one(
        'product.product',
        string='POS Product',
        required=True,
    )
    pos_session_id = fields.Many2one(
        'pos.session',
        string='POS Session',
        readonly=True,
    )
    nuvei_exchange_id = fields.Char(
        string='Nuvei Exchange ID',
        readonly=True,
        copy=False,
    )
    nuvei_last_status = fields.Char(
        string='Last Status',
        readonly=True,
        copy=False,
    )
    nuvei_is_processing = fields.Boolean(
        string='Processing',
        readonly=True,
        copy=False,
    )
    processing_message = fields.Char(
        string='Processing Message',
        readonly=True,
        copy=False,
    )
    invoice_line_ids = fields.Many2many(
        'account.move.line',
        string='Invoice Items',
        compute='_compute_invoice_line_ids',
        readonly=True,
    )

    @api.depends('invoice_id')
    def _compute_invoice_line_ids(self):
        for wizard in self:
            wizard.invoice_line_ids = wizard.invoice_id.invoice_line_ids

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        invoice = self.env['account.move'].browse(res.get('invoice_id'))
        if invoice:
            res.setdefault('amount', invoice.amount_residual)

            # Usar el producto de la factura (primera línea, sin restricción de POS)
            if invoice.invoice_line_ids:
                first_line = invoice.invoice_line_ids[0]
                if first_line.product_id:
                    res.setdefault('pos_product_id', first_line.product_id.id)

        payment_method = self.env['pos.payment.method'].search([
            ('use_payment_terminal', '=', 'nuvei')
        ], limit=1)
        if payment_method:
            res.setdefault('payment_method_id', payment_method.id)

        pos_config = self._find_pos_config(payment_method)
        if pos_config:
            res.setdefault('pos_config_id', pos_config.id)
            session = self._find_open_session(pos_config)
            if session:
                res.setdefault('pos_session_id', session.id)

        return res

    @api.onchange('pos_config_id')
    def _onchange_pos_config_id(self):
        self.pos_session_id = False
        if self.pos_config_id:
            session = self._find_open_session(self.pos_config_id)
            if session:
                self.pos_session_id = session.id

    def _find_pos_config(self, payment_method):
        if not payment_method:
            return False
        return self.env['pos.config'].search([
            ('payment_method_ids', 'in', payment_method.id),
        ], limit=1)

    def _find_open_session(self, pos_config):
        if not pos_config:
            return False
        return self.env['pos.session'].search([
            ('config_id', '=', pos_config.id),
            ('state', 'in', ['opening_control', 'opened']),
        ], order='id desc', limit=1)

    def action_pay_terminal(self):
        self.ensure_one()
        if self.nuvei_is_processing:
            raise UserError(_('There is already a transaction in process. Use Refresh or Cancel.'))
        invoice = self.invoice_id

        if invoice.move_type != 'out_invoice':
            raise UserError(_('Pay Terminal is only available for customer invoices.'))
        if invoice.state != 'posted':
            raise UserError(_('The invoice must be posted before paying with the terminal.'))
        if invoice.amount_residual <= 0:
            raise UserError(_('The invoice is already paid.'))
        if self.amount <= 0:
            raise UserError(_('The amount must be greater than zero.'))
        if self.amount > invoice.amount_residual:
            raise UserError(_('The amount cannot exceed the residual amount.'))

        if not self.payment_method_id or self.payment_method_id.use_payment_terminal != 'nuvei':
            raise UserError(_('Please select a Nuvei payment method.'))

        if not self.pos_config_id:
            raise UserError(_('Please select a POS configuration.'))
        if self.payment_method_id not in self.pos_config_id.payment_method_ids:
            raise UserError(_('The selected payment method is not available in this POS configuration.'))

        journal = self.payment_method_id.journal_id
        if not journal:
            raise UserError(_('The payment method must have a journal configured.'))

        self.nuvei_is_processing = True
        self.processing_message = 'Sending payment request to terminal...'

        payment_result = self._process_nuvei_payment(
            self.payment_method_id,
            self.amount,
            invoice,
        )

        if payment_result.get('exchange_id'):
            self.nuvei_exchange_id = payment_result.get('exchange_id')

        # El servicio gestiona solicitud + polling internamente
        # Si no fue completado, actualizar estado y recargar wizard
        if not payment_result.get('completed'):
            self._update_processing_state(payment_result, is_processing=True)
            return self._reload_wizard()

        #  VALIDACIONES DE ESTADO FINAL
        # Verificar si fue cancelada, declinada o aprobada

        if payment_result.get('cancelled'):
            # Transacción cancelada desde la terminal
            self._update_processing_state(payment_result, is_processing=False)
            status_code = payment_result.get('status', 'TXNC')
            raise UserError(_(
                'Payment transaction was cancelled (Status: %(status)s).\n'
                'The invoice remains unpaid.',
                status=status_code
            ))

        if payment_result.get('declined'):
            # Transacción rechazada por el banco/procesador
            self._update_processing_state(payment_result, is_processing=False)
            status_code = payment_result.get('status', 'DECL')
            error_msg = payment_result.get('error_message', 'Declined by issuer')
            raise UserError(_(
                'Payment was declined by the terminal (Status: %(status)s).\n'
                'Details: %(error)s\n'
                'The invoice remains unpaid.',
                status=status_code,
                error=error_msg,
            ))

        if not payment_result.get('success') or not payment_result.get('approved'):
            # Cualquier otro error o estado no-aprobado
            self._update_processing_state(payment_result, is_processing=False)
            status_code = payment_result.get('status', 'ERROR')
            error_msg = payment_result.get('error_message', 'Unknown error')
            raise UserError(_(
                'Payment processing failed (Status: %(status)s).\n'
                'Details: %(error)s\n'
                'The invoice remains unpaid.',
                status=status_code,
                error=error_msg,
            ))

        payments = self._register_invoice_payment(invoice, journal, self.amount)

        pos_order = None
        pos_payment = None
        if self.pos_session_id:
            pos_order, pos_payment = self._create_pos_records(invoice, payment_result)

        invoice.sudo().write({
            'nuvei_pos_order_id': pos_order.id if pos_order else False,
            'nuvei_pos_payment_id': pos_payment.id if pos_payment else False,
            'nuvei_exchange_id': payment_result.get('exchange_id'),
            'nuvei_payment_method_id': self.payment_method_id.id,
            'nuvei_payment_date': fields.Datetime.now(),
        })

        self.nuvei_is_processing = False
        self.processing_message = ''

        return {'type': 'ir.actions.act_window_close'}

    def _process_nuvei_payment(self, payment_method, amount, invoice):
        pos_payment_method = self.pos_config_id.payment_method_ids.filtered(
            lambda pm: pm.use_payment_terminal == 'nuvei'
        )
        if not pos_payment_method:
            pos_payment_method = payment_method
        else:
            pos_payment_method = pos_payment_method[0]

        nuvei_request = NuveiPosRequest(pos_payment_method)
        clean_invoice_name = invoice.name.replace('/', '-').replace(' ', '') if invoice.name else str(invoice.id)
        invoice_number = f"{invoice.id}-{clean_invoice_name[:15]}"

        # Intentar usar servicio si está disponible
        try:
            from nuvei_terminal_payment.models.nuvei_payment_service import NuveiPaymentService

            payment_service = NuveiPaymentService(nuvei_request)
            result = payment_service.process_payment(
                amount=amount,
                invoice_number=invoice_number,
                cashier_id='',
                timeout_seconds=payment_method.nuvei_timeout or 120,
                transaction_type='CRDP',
            )
            return result

        except ImportError:
            # Fallback: si nuvei_terminal_payment no está instalado, usar NuveiPosRequest directamente
            import time

            request = nuvei_request.send_payment_request(
                amount=amount,
                transaction_type='CRDP',
                invoice_number=invoice_number,
                cashier_id='',
            )

            # Validación BUSY en respuesta inicial (AUTQ)
            if request.get('busy'):
                return {
                    'success': False,
                    'completed': False,
                    'error_message': 'Terminal is busy. Please retry the payment.',
                    'status': 'BUSY',
                }

            if request.get('error'):
                return {
                    'success': False,
                    'completed': False,
                    'error_message': request.get('message', 'Error sending payment request.'),
                    'status': 'ERROR',
                }

            exchange_id = request.get('exchange_id')
            timeout_seconds = payment_method.nuvei_timeout or 120
            deadline = time.time() + timeout_seconds
            busy_retry_count = 0
            max_busy_retries = 10

            while time.time() < deadline:
                status = nuvei_request.check_transaction_status(exchange_id)

                if status.get('error'):
                    return {
                        'success': False,
                        'completed': False,
                        'error_message': status.get('message', 'Error while checking payment status.'),
                        'status': 'ERROR',
                    }

                # Validación BUSY en polling (RETR)
                # Si el dispositivo está ocupado, esperar un poco más antes de reintentar
                # Esto NO es un error, es solo que el terminal está procesando otra solicitud
                if status.get('busy'):
                    busy_retry_count += 1
                    if busy_retry_count > max_busy_retries:
                        return {
                            'success': False,
                            'completed': False,
                            'exchange_id': exchange_id,
                            'status': 'BUSY',
                            'error_message': f'Terminal remained busy for too long ({max_busy_retries} retries)',
                        }
                    # Esperar más tiempo antes de reintentar cuando el terminal está ocupado
                    time.sleep(3)
                    continue

                if status.get('completed'):
                    status['exchange_id'] = exchange_id
                    return status

                time.sleep(2)

            # Timeout
            return {
                'success': False,
                'completed': False,
                'exchange_id': exchange_id,
                'status': 'TIMEOUT',
                'error_message': f'Payment polling timed out after {timeout_seconds}s',
            }

    def action_cancel_terminal(self):
        """Cancela una transacción en proceso."""
        self.ensure_one()
        if not self.nuvei_exchange_id:
            raise UserError(_('There is no transaction to cancel.'))

        self.processing_message = 'Cancelling transaction...'

        # Usar credenciales del POS Config
        pos_payment_method = self.pos_config_id.payment_method_ids.filtered(
            lambda pm: pm.use_payment_terminal == 'nuvei'
        )
        if not pos_payment_method:
            raise UserError(_('No Nuvei payment method configured in POS.'))

        nuvei_request = NuveiPosRequest(pos_payment_method[0])

        # Intentar usar servicio si está disponible
        try:
            from nuvei_terminal_payment.models.nuvei_payment_service import NuveiPaymentService
            payment_service = NuveiPaymentService(nuvei_request)
            result = payment_service.cancel_payment(self.nuvei_exchange_id)

            if not result.get('success'):
                self.processing_message = f"Cancel failed: {result.get('error_message', 'Unknown error')}"
                raise UserError(result.get('error_message', 'Error while cancelling payment.'))

        except ImportError:
            # Fallback: si nuvei_terminal_payment no está instalado
            result = nuvei_request.cancel_transaction(self.nuvei_exchange_id)

            if result.get('error'):
                self.processing_message = f"Cancel failed: {result.get('message', 'Unknown error')}"
                raise UserError(result.get('message', 'Error while cancelling payment.'))

        self.nuvei_is_processing = False
        self.nuvei_last_status = 'CANCELLED'
        self.processing_message = 'Transaction cancelled successfully'

        return self._reload_wizard()

    def _update_processing_state(self, status, is_processing=False):
        self.nuvei_exchange_id = status.get('exchange_id')
        self.nuvei_last_status = status.get('status') or ''
        self.nuvei_is_processing = is_processing

        # Setear mensaje de estado descriptivo
        if is_processing:
            status_code = self.nuvei_last_status or 'PROCESSING'
            self.processing_message = f'Waiting for terminal response... ({status_code})'
        else:
            self.processing_message = ''

    def _reload_wizard(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'nuvei.invoice.payment.wizard',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new',
        }

    def _register_invoice_payment(self, invoice, journal, amount):
        payment_register = self.env['account.payment.register'].with_context(
            active_model='account.move',
            active_ids=invoice.ids,
        ).create({
            'amount': amount,
            'journal_id': journal.id,
            'payment_date': fields.Date.context_today(self),
            'communication': invoice.name,
        })

        payments = payment_register._create_payments()
        if not payments:
            action = payment_register.action_create_payments()
            if action and action.get('res_id'):
                payments = self.env['account.payment'].browse(action['res_id'])
        return payments

    def _create_pos_records(self, invoice, payment_result):
        """Crea POS records si hay sesión abierta.
        Retorna (None, None) si no hay sesión (pago sin POS).
        """
        if not self.pos_session_id:
            return None, None

        product = self.pos_product_id
        if not product:
            raise UserError(_('Please select a POS product.'))

        pos_order = self.env['pos.order'].create({
            'name': invoice.name or 'Invoice Payment',
            'pos_reference': invoice.name or 'Invoice Payment',
            'company_id': invoice.company_id.id,
            'session_id': self.pos_session_id.id,
            'partner_id': invoice.partner_id.id,
            'amount_tax': 0.0,
            'amount_total': self.amount,
            'amount_paid': self.amount,
            'amount_return': 0.0,
            'lines': [Command.create({
                'name': invoice.name or 'Invoice Payment',
                'product_id': product.id,
                'price_unit': self.amount,
                'discount': 0.0,
                'qty': 1.0,
                'tax_ids': False,
                'price_subtotal': self.amount,
                'price_subtotal_incl': self.amount,
            })],
        })

        pos_payment_vals = {
            'pos_order_id': pos_order.id,
            'payment_method_id': self.payment_method_id.id,
            'amount': self.amount,
        }

        pos_payment_model = self.env['pos.payment']
        if 'payment_date' in pos_payment_model._fields:
            pos_payment_vals['payment_date'] = fields.Datetime.now()
        if 'payment_status' in pos_payment_model._fields:
            pos_payment_vals['payment_status'] = 'done'

        td = payment_result.get('transaction_data') or {}
        receipts = td.get('receipts') or []
        receipt_text = '\n\n'.join(r.get('content', '') for r in receipts if r.get('content'))

        pos_payment_vals.update({
            'nuvei_exchange_id': payment_result.get('exchange_id', ''),
            'nuvei_reference_no': td.get('terminal_reference', ''),
            'nuvei_auth_code': td.get('auth_code', ''),
            'nuvei_card_type': td.get('card_type', ''),
            'nuvei_card_number': td.get('card_number', ''),
            'nuvei_entry_mode': td.get('entry_mode', ''),
            'nuvei_approval_text': td.get('approval_text', ''),
            'nuvei_receipt_text': receipt_text,
        })

        pos_payment = pos_payment_model.create(pos_payment_vals)

        if 'state' in pos_order._fields:
            pos_order.state = 'paid'

        return pos_order, pos_payment
