# -*- coding: utf-8 -*-
"""Extiende account.move para pagos via terminal Nuvei."""
from odoo import api, fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    nuvei_pos_order_id = fields.Many2one(
        'pos.order',
        string='Nuvei POS Order',
        readonly=True,
        copy=False,
    )
    nuvei_pos_payment_id = fields.Many2one(
        'pos.payment',
        string='Nuvei POS Payment',
        readonly=True,
        copy=False,
    )
    nuvei_exchange_id = fields.Char(
        string='Nuvei Exchange ID',
        readonly=True,
        copy=False,
    )
    nuvei_payment_method_id = fields.Many2one(
        'pos.payment.method',
        string='Nuvei Payment Method',
        readonly=True,
        copy=False,
    )
    nuvei_payment_date = fields.Datetime(
        string='Nuvei Payment Date',
        readonly=True,
        copy=False,
    )
