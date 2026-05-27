# -*- coding: utf-8 -*-
"""
Tests unitarios para pos.payment.method (extensión Nuvei).

Valida la configuración, onchange de test_mode, y los métodos ORM
que son llamados desde el frontend JS.
"""
from unittest.mock import patch, MagicMock

from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestPosPaymentMethod(TransactionCase):
    """Tests para el modelo pos.payment.method con Nuvei."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.payment_method = cls.env['pos.payment.method'].create({
            'name': 'Nuvei Test',
            'use_payment_terminal': 'nuvei',
            'nuvei_device_id': 'TestPOS1',
            'nuvei_terminal_id': 'TestTerm1',
            'nuvei_authentication_key': 'test-key-123',
            'nuvei_merchant_id': '1234567890',
            'nuvei_api_url': 'https://terminal-sandbox.nuvei.com/omnichannel-cloudserver',
            'nuvei_test_mode': True,
            'nuvei_timeout': 120,
        })

    # -------------------------------------------------------------------------
    # Tests de configuración
    # -------------------------------------------------------------------------

    def test_nuvei_terminal_selection(self):
        """Verifica que 'nuvei' aparece en las opciones de terminal."""
        options = self.payment_method._get_payment_terminal_selection()
        terminal_keys = [opt[0] for opt in options]
        self.assertIn('nuvei', terminal_keys)

    def test_nuvei_fields_exist(self):
        """Verifica que los campos Nuvei se crearon correctamente."""
        self.assertEqual(self.payment_method.nuvei_device_id, 'TestPOS1')
        self.assertEqual(self.payment_method.nuvei_terminal_id, 'TestTerm1')
        self.assertEqual(self.payment_method.nuvei_merchant_id, '1234567890')
        self.assertTrue(self.payment_method.nuvei_test_mode)
        self.assertEqual(self.payment_method.nuvei_timeout, 120)

    def test_onchange_test_mode_to_production(self):
        """Verifica que al desactivar test_mode se actualiza la URL a producción."""
        self.payment_method.nuvei_test_mode = False
        self.payment_method._onchange_nuvei_test_mode()
        self.assertIn('terminal.nuvei.com', self.payment_method.nuvei_api_url)
        self.assertNotIn('sandbox', self.payment_method.nuvei_api_url)

    def test_onchange_test_mode_to_sandbox(self):
        """Verifica que al activar test_mode se actualiza la URL a sandbox."""
        self.payment_method.nuvei_test_mode = True
        self.payment_method._onchange_nuvei_test_mode()
        self.assertIn('sandbox', self.payment_method.nuvei_api_url)

    # -------------------------------------------------------------------------
    # Tests de protección de escritura
    # -------------------------------------------------------------------------

    def test_write_protection_allows_latest_response(self):
        """Verifica que nuvei_latest_response se puede escribir siempre."""
        # _is_write_forbidden debe excluir nuvei_latest_response
        result = self.payment_method._is_write_forbidden({'nuvei_latest_response'})
        self.assertFalse(result)

    # -------------------------------------------------------------------------
    # Tests de test_nuvei_connection
    # -------------------------------------------------------------------------

    def test_connection_missing_fields(self):
        """Verifica error si faltan campos de configuración."""
        pm = self.env['pos.payment.method'].create({
            'name': 'Nuvei Incompleto',
            'use_payment_terminal': 'nuvei',
        })
        with self.assertRaises(UserError):
            pm.test_nuvei_connection()

    @patch('odoo.addons.pos_cloudlion_nuvei.models.pos_payment_method.NuveiPosRequest')
    def test_connection_success(self, MockNuvei):
        """Verifica test de conexión exitoso retorna notificación."""
        mock_instance = MockNuvei.return_value
        mock_instance.test_connection.return_value = {
            'success': True,
            'response_code': 'APPR',
            'response': {'test': 'data'},
        }

        result = self.payment_method.test_nuvei_connection()
        self.assertEqual(result['type'], 'ir.actions.client')
        self.assertEqual(result['tag'], 'display_notification')
        self.assertEqual(result['params']['type'], 'success')

    @patch('odoo.addons.pos_cloudlion_nuvei.models.pos_payment_method.NuveiPosRequest')
    def test_connection_failure(self, MockNuvei):
        """Verifica que conexión fallida lanza UserError."""
        mock_instance = MockNuvei.return_value
        mock_instance.test_connection.return_value = {
            'error': True,
            'message': 'Credenciales inválidas',
        }

        with self.assertRaises(UserError):
            self.payment_method.test_nuvei_connection()

    # -------------------------------------------------------------------------
    # Tests de métodos ORM (llamados desde JS)
    # -------------------------------------------------------------------------

    @patch('odoo.addons.pos_cloudlion_nuvei.models.pos_payment_method.NuveiPosRequest')
    def test_make_payment_request_returns_timeout(self, MockNuvei):
        """Verifica que nuvei_make_payment_request incluye timeout en respuesta."""
        mock_instance = MockNuvei.return_value
        mock_instance.send_payment_request.return_value = {
            'success': True,
            'exchange_id': 'test-eid',
            'completed': False,
            'status': 'INPR',
            'response': {'test': 'data'},
        }

        result = self.payment_method.nuvei_make_payment_request({
            'amount': 10.00,
            'transaction_type': 'CRDP',
        })

        self.assertTrue(result['success'])
        self.assertEqual(result['timeout'], 120)

    @patch('odoo.addons.pos_cloudlion_nuvei.models.pos_payment_method.NuveiPosRequest')
    def test_fetch_payment_status_returns_data(self, MockNuvei):
        """Verifica que nuvei_fetch_payment_status retorna datos correctos."""
        mock_instance = MockNuvei.return_value
        mock_instance.check_transaction_status.return_value = {
            'success': True,
            'completed': True,
            'status': 'APPR',
            'transaction_data': {'auth_code': 'A123'},
            'response': {'test': 'data'},
        }

        result = self.payment_method.nuvei_fetch_payment_status({
            'exchange_id': 'test-eid',
        })

        self.assertTrue(result['success'])
        self.assertTrue(result['completed'])
        self.assertEqual(result['transaction_data']['auth_code'], 'A123')

    @patch('odoo.addons.pos_cloudlion_nuvei.models.pos_payment_method.NuveiPosRequest')
    def test_cancel_payment(self, MockNuvei):
        """Verifica que nuvei_cancel_payment retorna resultado limpio."""
        mock_instance = MockNuvei.return_value
        mock_instance.cancel_transaction.return_value = {
            'success': True,
            'response': {'cancelled': True},
        }

        result = self.payment_method.nuvei_cancel_payment({
            'exchange_id': 'test-eid',
        })

        self.assertTrue(result['success'])
        self.assertFalse(result.get('error', False))

    @patch('odoo.addons.pos_cloudlion_nuvei.models.pos_payment_method.NuveiPosRequest')
    def test_make_refund_request(self, MockNuvei):
        """Verifica que nuvei_make_refund_request retorna datos correctos."""
        mock_instance = MockNuvei.return_value
        mock_instance.send_void_request.return_value = {
            'success': True,
            'exchange_id': 'void-eid',
            'response': {'test': 'data'},
            'transaction_data': {'auth_code': 'V999'},
        }

        result = self.payment_method.nuvei_make_refund_request({
            'original_transaction_id': 'ORIG-001',
            'transaction_type': 'CRDP',
        })

        self.assertTrue(result['success'])
        self.assertEqual(result['exchange_id'], 'void-eid')
