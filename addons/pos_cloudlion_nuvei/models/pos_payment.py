# -*- coding: utf-8 -*-
"""
Extensión del modelo pos.payment para Nuvei.

Agrega campos para almacenar referencias de transacción devueltas
por el terminal Nuvei. Estos campos se usan para:
  - Identificar la transacción en Nuvei Cloud (exchange_id)
  - Referencia para anulaciones/reversas (reference_no)

Sigue el patrón de pos_razorpay y pos_viva_com: solo campos Char,
sin override de métodos (el mixin base ya carga todos los campos al POS).
"""
from odoo import fields, models


class PosPayment(models.Model):
    _inherit = 'pos.payment'

    # Identificador único del intercambio de mensajes con Nuvei.
    # Generado por el POS al iniciar la transacción.
    # Necesario para consultar estado (SASQ + RETR) y cancelar (CUCL + FCXL).
    nuvei_exchange_id = fields.Char(
        string='Nuvei Exchange ID',
        help='Unique message exchange ID with Nuvei Cloud.',
        copy=False,
        readonly=True,
    )

    # Referencia de la transacción devuelta por el terminal en la respuesta.
    # Necesario para enviar anulaciones (RVSL) de la transacción.
    nuvei_reference_no = fields.Char(
        string='Nuvei Reference No.',
        help='Transaction reference returned by the Nuvei terminal.',
        copy=False,
        readonly=True,
    )

    # Datos del recibo del terminal para impresión.
    nuvei_card_type = fields.Char(
        string='Nuvei Card Type',
        help='Card type (for example: VISA CREDIT, MC DEBIT).',
        copy=False,
        readonly=True,
    )
    nuvei_card_number = fields.Char(
        string='Nuvei Card Number',
        help='Masked card number (for example: ************1234).',
        copy=False,
        readonly=True,
    )
    nuvei_auth_code = fields.Char(
        string='Nuvei Auth Code',
        help='Issuer authorization code.',
        copy=False,
        readonly=True,
    )
    nuvei_entry_mode = fields.Char(
        string='Nuvei Entry Mode',
        help='Entry mode: C=Chip, S=Swipe, T=Tap, M=Manual.',
        copy=False,
        readonly=True,
    )
    nuvei_approval_text = fields.Char(
        string='Nuvei Approval Text',
        help='ISO approval/decline text (for example: APPROVED 000).',
        copy=False,
        readonly=True,
    )
    nuvei_receipt_text = fields.Text(
        string='Nuvei Receipt',
        help='Texto del recibo del terminal Nuvei.',
        copy=False,
        readonly=True,
    )
