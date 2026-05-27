# -*- coding: utf-8 -*-
"""
Tests unitarios para NuveiPosRequest.

Mockea requests.post para validar:
  - Generación correcta de payloads AUTQ, RFNQ, FMPV, RCLQ
  - Parseo de respuestas AUTP (aprobada, rechazada)
  - Manejo de errores de conexión
  - Mapeo correcto de transaction_type a messageFunction
  - Parseo de batch close
"""
import json
import uuid
from unittest.mock import patch, MagicMock

from odoo.tests.common import TransactionCase


class MockPaymentMethod:
    """Mock de pos.payment.method con campos Nuvei."""
    def __init__(self, **kwargs):
        self.nuvei_device_id = kwargs.get('device_id', 'TestPOS1')
        self.nuvei_terminal_id = kwargs.get('terminal_id', 'TestTerm1')
        self.nuvei_authentication_key = kwargs.get('auth_key', 'test-key-123')
        self.nuvei_merchant_id = kwargs.get('merchant_id', '1234567890')
        self.nuvei_api_url = kwargs.get('api_url', 'https://terminal-sandbox.nuvei.com/omnichannel-cloudserver')
        self.nuvei_test_mode = kwargs.get('test_mode', True)
        self.nuvei_timeout = kwargs.get('timeout', 120)


class TestNuveiPosRequest(TransactionCase):
    """Tests para la clase helper NuveiPosRequest."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Importar aquí para evitar problemas de import circular
        from odoo.addons.pos_cloudlion_nuvei.models.nuvei_pos_request import NuveiPosRequest
        cls.NuveiPosRequest = NuveiPosRequest

    def _create_nuvei(self, **kwargs):
        """Crea una instancia de NuveiPosRequest con mock payment method."""
        pm = MockPaymentMethod(**kwargs)
        return self.NuveiPosRequest(pm)

    def _mock_response(self, status_code=200, json_data=None):
        """Crea un mock de requests.Response."""
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.json.return_value = json_data or {}
        mock_resp.text = json.dumps(json_data or {})
        mock_resp.content = mock_resp.text.encode()
        mock_resp.headers = {'Content-Type': 'application/json'}
        return mock_resp

    # -------------------------------------------------------------------------
    # Tests de inicialización
    # -------------------------------------------------------------------------

    def test_init_with_test_mode(self):
        """Verifica que test_mode=True usa URL sandbox."""
        nuvei = self._create_nuvei(test_mode=True, api_url='')
        self.assertIn('sandbox', nuvei.base_url)

    def test_init_with_production_mode(self):
        """Verifica que test_mode=False usa URL producción."""
        nuvei = self._create_nuvei(test_mode=False, api_url='')
        self.assertNotIn('sandbox', nuvei.base_url)
        self.assertIn('terminal.nuvei.com', nuvei.base_url)

    def test_init_custom_url_overrides(self):
        """Verifica que una URL personalizada tiene prioridad."""
        custom = 'https://custom.nuvei.com/api'
        nuvei = self._create_nuvei(api_url=custom)
        self.assertEqual(nuvei.base_url, custom)

    def test_init_credentials(self):
        """Verifica que las credenciales se asignan correctamente."""
        nuvei = self._create_nuvei(
            device_id='POS-001',
            terminal_id='TERM-001',
            auth_key='secret-key',
            merchant_id='M-12345',
        )
        self.assertEqual(nuvei.device_id, 'POS-001')
        self.assertEqual(nuvei.terminal_id, 'TERM-001')
        self.assertEqual(nuvei.auth_key, 'secret-key')
        self.assertEqual(nuvei.merchant_id, 'M-12345')

    # -------------------------------------------------------------------------
    # Tests de construcción de header
    # -------------------------------------------------------------------------

    def test_build_header_structure(self):
        """Verifica estructura del header según spec ISO20022."""
        nuvei = self._create_nuvei()
        header = nuvei._build_header('AUTQ', 'test-exchange-123')

        self.assertEqual(header['messageFunction'], 'AUTQ')
        self.assertEqual(header['protocolVersion'], '2.0')
        self.assertEqual(header['exchangeIdentification'], 'test-exchange-123')
        self.assertIn('creationDateTime', header)
        self.assertEqual(header['initiatingParty']['identification'], 'TestPOS1')
        self.assertEqual(header['initiatingParty']['type'], 'PID')
        self.assertEqual(header['recipientParty']['identification'], 'TestTerm1')
        self.assertEqual(header['recipientParty']['type'], 'TID')

    def test_build_header_auto_exchange_id(self):
        """Verifica que se genera exchange_id automáticamente si no se pasa."""
        nuvei = self._create_nuvei()
        header = nuvei._build_header('AUTQ')
        self.assertIsNotNone(header['exchangeIdentification'])
        self.assertTrue(len(header['exchangeIdentification']) > 0)

    # -------------------------------------------------------------------------
    # Tests de message function mapping
    # -------------------------------------------------------------------------

    def test_message_function_mapping(self):
        """Verifica mapeo transaction_type → messageFunction."""
        nuvei = self._create_nuvei()
        self.assertEqual(nuvei._get_request_message_function('CRDP'), 'AUTQ')
        self.assertEqual(nuvei._get_request_message_function('RFND'), 'RFNQ')
        self.assertEqual(nuvei._get_request_message_function('RQPA'), 'FAUQ')
        self.assertEqual(nuvei._get_request_message_function('CMPN'), 'CMPV')

    def test_message_function_default(self):
        """Verifica que tipo desconocido devuelve AUTQ por defecto."""
        nuvei = self._create_nuvei()
        self.assertEqual(nuvei._get_request_message_function('UNKNOWN'), 'AUTQ')

    # -------------------------------------------------------------------------
    # Tests de envío de pago (AUTQ)
    # -------------------------------------------------------------------------

    @patch('odoo.addons.pos_cloudlion_nuvei.models.nuvei_pos_request.requests.post')
    def test_send_payment_request_payload(self, mock_post):
        """Verifica estructura del payload AUTQ enviado a /services."""
        mock_post.return_value = self._mock_response(200, {
            'sessionManagementResponse': {
                'sessionResponse': {'response': 'INPR'}
            }
        })

        nuvei = self._create_nuvei()
        result = nuvei.send_payment_request(
            amount=25.50,
            transaction_type='CRDP',
            invoice_number='INV-001',
            cashier_id='CASHIER1',
            exchange_id='test-eid-123',
        )

        # Verificar que se llamó POST /services
        call_args = mock_post.call_args
        url = call_args[0][0]
        self.assertIn('/services', url)

        # Verificar payload
        payload = call_args[1]['json']
        self.assertIn('header', payload)
        self.assertIn('serviceRequest', payload)
        self.assertNotIn('OCserviceRequest', payload)  # SIN wrapper

        # Verificar header
        self.assertEqual(payload['header']['messageFunction'], 'AUTQ')
        self.assertEqual(payload['header']['exchangeIdentification'], 'test-eid-123')

        # Verificar serviceRequest
        sr = payload['serviceRequest']
        self.assertEqual(sr['paymentRequest']['transactionType'], 'CRDP')
        self.assertEqual(sr['paymentRequest']['transactionDetails']['totalAmount'], '25.50')
        self.assertEqual(sr['context']['saleContext']['invoiceNumber'], 'INV-001')
        self.assertEqual(sr['context']['saleContext']['cashierIdentification'], 'CASHIER1')

        # Verificar resultado
        self.assertTrue(result['success'])
        self.assertEqual(result['exchange_id'], 'test-eid-123')
        self.assertFalse(result['completed'])

    @patch('odoo.addons.pos_cloudlion_nuvei.models.nuvei_pos_request.requests.post')
    def test_send_refund_request_uses_rfnq(self, mock_post):
        """Verifica que RFND usa messageFunction RFNQ."""
        mock_post.return_value = self._mock_response(200, {
            'sessionManagementResponse': {
                'sessionResponse': {'response': 'INPR'}
            }
        })

        nuvei = self._create_nuvei()
        nuvei.send_payment_request(amount=10.00, transaction_type='RFND')

        payload = mock_post.call_args[1]['json']
        self.assertEqual(payload['header']['messageFunction'], 'RFNQ')

    @patch('odoo.addons.pos_cloudlion_nuvei.models.nuvei_pos_request.requests.post')
    def test_send_payment_connection_error(self, mock_post):
        """Verifica manejo de error de conexión."""
        import requests as req
        mock_post.side_effect = req.exceptions.ConnectionError('Connection refused')

        nuvei = self._create_nuvei()
        result = nuvei.send_payment_request(amount=10.00)

        self.assertTrue(result['error'])
        self.assertIn('conexión', result['message'].lower())

    # -------------------------------------------------------------------------
    # Tests de polling (RETR)
    # -------------------------------------------------------------------------

    @patch('odoo.addons.pos_cloudlion_nuvei.models.nuvei_pos_request.requests.post')
    def test_check_status_in_process(self, mock_post):
        """Verifica respuesta ACPT (transacción en proceso)."""
        mock_post.return_value = self._mock_response(201, {
            'header': {'messageFunction': 'SASP'},
            'sessionManagementResponse': {
                'transactionInProcess': {
                    'transactionStatus': 'ACPT',
                    'exchangeIdentification': 'test-eid-123',
                }
            }
        })

        nuvei = self._create_nuvei()
        result = nuvei.check_transaction_status('test-eid-123')

        self.assertTrue(result['success'])
        self.assertFalse(result['completed'])
        self.assertEqual(result['status'], 'ACPT')

    @patch('odoo.addons.pos_cloudlion_nuvei.models.nuvei_pos_request.requests.post')
    def test_check_status_completed_approved(self, mock_post):
        """Verifica parseo de respuesta AUTP aprobada."""
        mock_post.return_value = self._mock_response(201, {
            'header': {'messageFunction': 'AUTP'},
            'serviceResponse': {
                'paymentResponse': [{
                    'retailerPaymentResult': {
                        'transactionType': 'CRDP',
                        'transactionResponse': {
                            'authorisationResult': {
                                'responseToAuthorisation': {'response': 'APPR'},
                                'authorisationCode': 'A12345',
                            },
                            'receiptDetails': {
                                'mskPan': '************1234',
                                'cardLbl': 'VISA CREDIT',
                                'cardAID': 'A0000000031010',
                                'apprdeclISO': 'APPROVED 000',
                                'cardDataNtryMd': 'C',
                                'hostInvoice': 'INV001',
                                'hostSequence': 'SEQ001',
                            },
                            'transactionDetails': {
                                'totalAmount': '25.50',
                            },
                        },
                    },
                    'saleTransactionIdentification': {
                        'transactionReference': 'SALE-REF-001',
                    },
                    'POITransactionIdentification': {
                        'transactionReference': 'POI-REF-001',
                    },
                    'receipt': [
                        {'documentQualifier': 'MRCPT', 'outputContent': 'Merchant Copy'},
                        {'documentQualifier': 'CRCPT', 'outputContent': 'Customer Copy'},
                    ],
                }],
            },
        })

        nuvei = self._create_nuvei()
        result = nuvei.check_transaction_status('test-eid-123')

        self.assertTrue(result['success'])
        self.assertTrue(result['completed'])
        self.assertEqual(result['status'], 'APPR')

        td = result['transaction_data']
        self.assertEqual(td['auth_code'], 'A12345')
        self.assertEqual(td['card_number'], '************1234')
        self.assertEqual(td['card_type'], 'VISA CREDIT')
        self.assertEqual(td['card_aid'], 'A0000000031010')
        self.assertEqual(td['entry_mode'], 'C')
        self.assertEqual(td['approval_text'], 'APPROVED 000')
        self.assertEqual(td['sale_reference'], 'SALE-REF-001')
        self.assertEqual(td['terminal_reference'], 'POI-REF-001')
        self.assertEqual(td['total_amount'], '25.50')
        self.assertEqual(len(td['receipts']), 2)

    @patch('odoo.addons.pos_cloudlion_nuvei.models.nuvei_pos_request.requests.post')
    def test_check_status_empty_payment_response_bug_fix(self, mock_post):
        """
        BUG FIX: Valida que transacciones canceladas con paymentResponse vacío
        sean rechazadas (no aprobadas).

        Escenario: Terminal cancela la transacción antes de responder. Nuvei
        devuelve AUTP con serviceResponse.paymentResponse = [] (vacío).

        Antes del fix: completed=True, success=True ❌ (aprobaba la transacción)
        Después del fix: completed=True, cancelled=True, success=False ✓
        """
        mock_post.return_value = self._mock_response(201, {
            'header': {'messageFunction': 'AUTP'},
            'serviceResponse': {
                'response': {},
                'paymentResponse': [],  # ← VACÍO = cancelación desde terminal
            },
        })

        nuvei = self._create_nuvei()
        result = nuvei.check_transaction_status('test-eid-123')

        # Validar que se rechaza la transacción
        self.assertTrue(result['completed'], 'Debe marcar como completado')
        self.assertTrue(result['cancelled'], 'Debe marcar como cancelado')
        self.assertFalse(result['success'], 'NO debe marcar como exitoso')
        self.assertFalse(result.get('approved', False), 'NO debe marcar como aprobado')
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertEqual(result['message'], 'Transaction cancelled by terminal')

    @patch('odoo.addons.pos_cloudlion_nuvei.models.nuvei_pos_request.requests.post')
    def test_check_status_cancelled_from_terminal(self, mock_post):
        """Verifica detección de cancelación desde terminal (TXCN)."""
        mock_post.return_value = self._mock_response(201, {
            'header': {'messageFunction': 'TXCN'},
            'sessionManagementResponse': {
                'sessionResponse': {'response': 'APPR'}
            }
        })

        nuvei = self._create_nuvei()
        result = nuvei.check_transaction_status('test-eid-123')

        self.assertTrue(result['completed'])
        self.assertTrue(result['cancelled'])
        self.assertEqual(result['status'], 'TXCN')

    @patch('odoo.addons.pos_cloudlion_nuvei.models.nuvei_pos_request.requests.post')
    def test_check_status_notf(self, mock_post):
        """Verifica respuesta NOTF (sin respuesta aún)."""
        mock_post.return_value = self._mock_response(201, {
            'header': {'messageFunction': 'SASP'},
            'sessionManagementResponse': {
                'sessionResponse': {'response': 'NOTF'}
            }
        })

        nuvei = self._create_nuvei()
        result = nuvei.check_transaction_status('test-eid-123')

        self.assertTrue(result['success'])
        self.assertFalse(result['completed'])
        self.assertEqual(result['status'], 'NOTF')

    # -------------------------------------------------------------------------
    # Tests de cancelación (FCXL)
    # -------------------------------------------------------------------------

    @patch('odoo.addons.pos_cloudlion_nuvei.models.nuvei_pos_request.requests.post')
    def test_cancel_transaction_payload(self, mock_post):
        """Verifica payload de cancelación FCXL via /session."""
        mock_post.return_value = self._mock_response(201, {
            'header': {'messageFunction': 'SASP'},
            'sessionManagementResponse': {'sessionResponse': {'response': 'APPR'}}
        })

        nuvei = self._create_nuvei()
        result = nuvei.cancel_transaction('test-eid-to-cancel')

        self.assertTrue(result['success'])

        payload = mock_post.call_args[1]['json']
        self.assertIn('/session', mock_post.call_args[0][0])
        smr = payload['sessionManagementRequest']
        self.assertEqual(smr['POSComponent']['POSGroupIdentification']['exchangeAction'], 'FCXL')
        self.assertEqual(smr['POSComponent']['POSGroupIdentification']['exchangeIdentification'], 'test-eid-to-cancel')

    # -------------------------------------------------------------------------
    # Tests de void (FMPV)
    # -------------------------------------------------------------------------

    @patch('odoo.addons.pos_cloudlion_nuvei.models.nuvei_pos_request.requests.post')
    def test_void_request_payload(self, mock_post):
        """Verifica payload de void FMPV via /services."""
        mock_post.return_value = self._mock_response(200, {
            'header': {'messageFunction': 'FMPV'},
            'serviceResponse': {
                'paymentResponse': [{
                    'retailerPaymentResult': {
                        'transactionType': 'CRDP',
                        'transactionResponse': {
                            'authorisationResult': {
                                'responseToAuthorisation': {'response': 'APPR'},
                                'authorisationCode': 'V99999',
                            },
                            'receiptDetails': {'mskPan': '****5678'},
                        },
                    },
                }],
            },
        })

        nuvei = self._create_nuvei()
        result = nuvei.send_void_request(
            original_transaction_id='ORIG-TX-001',
            transaction_type='CRDP',
        )

        self.assertTrue(result['success'])
        # Verificar que se parsea la respuesta del void
        self.assertIn('transaction_data', result)

        payload = mock_post.call_args[1]['json']
        self.assertEqual(payload['header']['messageFunction'], 'FMPV')
        self.assertIn('reversalRequest', payload['serviceRequest'])

    # -------------------------------------------------------------------------
    # Tests de batch close (RCLQ)
    # -------------------------------------------------------------------------

    @patch('odoo.addons.pos_cloudlion_nuvei.models.nuvei_pos_request.requests.post')
    def test_batch_close_payload(self, mock_post):
        """Verifica payload de batch close RCLQ via /services."""
        mock_post.return_value = self._mock_response(200, {
            'header': {'messageFunction': 'RCLP'},
            'serviceResponse': {
                'batchResponse': {
                    'response': 'APPR',
                    'batchTotals': {
                        'salesCount': 5,
                        'salesAmount': '150.00',
                        'refundsCount': 1,
                        'refundsAmount': '25.00',
                        'netCount': 4,
                        'netAmount': '125.00',
                    },
                },
            },
        })

        nuvei = self._create_nuvei()
        result = nuvei.batch_close()

        self.assertTrue(result['success'])
        self.assertIn('batch_data', result)
        bd = result['batch_data']
        self.assertEqual(bd['sales_count'], 5)
        self.assertEqual(bd['sales_amount'], '150.00')
        self.assertEqual(bd['refunds_count'], 1)
        self.assertEqual(bd['net_amount'], '125.00')

    # -------------------------------------------------------------------------
    # Tests de test connection (NOTI)
    # -------------------------------------------------------------------------

    @patch('odoo.addons.pos_cloudlion_nuvei.models.nuvei_pos_request.requests.post')
    def test_connection_success(self, mock_post):
        """Verifica test de conexión exitoso (APPR)."""
        mock_post.return_value = self._mock_response(201, {
            'header': {'messageFunction': 'SASP'},
            'sessionManagementResponse': {
                'sessionResponse': {'response': 'APPR'}
            }
        })

        nuvei = self._create_nuvei()
        result = nuvei.test_connection()

        self.assertTrue(result['success'])
        self.assertEqual(result['response_code'], 'APPR')

    @patch('odoo.addons.pos_cloudlion_nuvei.models.nuvei_pos_request.requests.post')
    def test_connection_invalid_credentials(self, mock_post):
        """Verifica error INTP (credenciales inválidas)."""
        mock_post.return_value = self._mock_response(201, {
            'header': {'messageFunction': 'SASP'},
            'sessionManagementResponse': {
                'sessionResponse': {'response': 'INTP'}
            }
        })

        nuvei = self._create_nuvei()
        result = nuvei.test_connection()

        self.assertTrue(result['error'])
        self.assertIn('inválid', result['message'].lower())

    @patch('odoo.addons.pos_cloudlion_nuvei.models.nuvei_pos_request.requests.post')
    def test_connection_terminal_not_found(self, mock_post):
        """Verifica error RCPP (terminal no encontrado)."""
        mock_post.return_value = self._mock_response(201, {
            'header': {'messageFunction': 'SASP'},
            'sessionManagementResponse': {
                'sessionResponse': {'response': 'RCPP'}
            }
        })

        nuvei = self._create_nuvei()
        result = nuvei.test_connection()

        self.assertTrue(result['error'])
        self.assertIn('terminal', result['message'].lower())

    @patch('odoo.addons.pos_cloudlion_nuvei.models.nuvei_pos_request.requests.post')
    def test_connection_unmapped_pos(self, mock_post):
        """Verifica error UNMP (POS no asociado al terminal)."""
        mock_post.return_value = self._mock_response(201, {
            'header': {'messageFunction': 'SASP'},
            'sessionManagementResponse': {
                'sessionResponse': {'response': 'UNMP'}
            }
        })

        nuvei = self._create_nuvei()
        result = nuvei.test_connection()

        self.assertTrue(result['error'])
        self.assertIn('asociado', result['message'].lower())

    @patch('odoo.addons.pos_cloudlion_nuvei.models.nuvei_pos_request.requests.post')
    def test_connection_with_pending_transaction(self, mock_post):
        """Verifica detección de transacción pendiente en test connection."""
        mock_post.return_value = self._mock_response(201, {
            'header': {'messageFunction': 'SASP'},
            'sessionManagementResponse': {
                'sessionResponse': {'response': 'APPR'},
                'transactionInProcess': {
                    'exchangeIdentification': 'pending-tx-123',
                    'transactionStatus': 'ACPT',
                },
            }
        })

        nuvei = self._create_nuvei()
        result = nuvei.test_connection()

        self.assertTrue(result['success'])
        self.assertEqual(result['pending_exchange'], 'pending-tx-123')

    # -------------------------------------------------------------------------
    # Tests de HTTP errors
    # -------------------------------------------------------------------------

    @patch('odoo.addons.pos_cloudlion_nuvei.models.nuvei_pos_request.requests.post')
    def test_http_error_raises(self, mock_post):
        """Verifica que errores HTTP se manejan correctamente."""
        mock_resp = self._mock_response(400, {'error': 'Bad Request'})
        mock_resp.raise_for_status.side_effect = Exception('400 Client Error')
        mock_post.return_value = mock_resp

        nuvei = self._create_nuvei()
        result = nuvei.send_payment_request(amount=10.00)

        # Debe retornar error, no explotar
        self.assertTrue(result.get('error'))
