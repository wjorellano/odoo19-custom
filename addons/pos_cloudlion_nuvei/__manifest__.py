# -*- coding: utf-8 -*-
{
    'name': 'POS CloudLion x Nuvei',
    'version': '1.1',
    'category': 'Sales/Point of Sale',
    'sequence': 6,
    'summary': 'Integrate your POS with Nuvei OMNI Channel ISO20022 payment terminals',
    'author': 'CloudLion',
    'description': """
Nuvei Payment Terminal Integration for Point of Sale
=========================================================

Connect your Odoo POS with Nuvei terminals using the
OMNI Channel ISO20022 v2.51 via API RESTful HTTPS.

Supported transaction types:
- CRDP: Card payment (credit/debit)
- RFND: Refund
- RQPA: Pre-authorization
- CMPN: Complete pre-authorization
- RVSL: Void/Reversal

Requirements:
- Nuvei merchant account with OMNI Channel access
- Nuvei Terminal ID (TID) and Authentication Key
- Device ID (PID) configured in Nuvei Cloud Service
""",
    # Depende de POS y facturación
    # nuvei_terminal_payment es OPCIONAL (lazy import en wizard)
    'depends': ['point_of_sale', 'account'],
    # Only POS payment method view (no security or config settings needed)
    'data': [
        'data/ir_groups.xml',  # ← Debe cargarse PRIMERO (define los grupos)
        'security/ir.model.access.csv',  # ← Luego el CSV que los referencia
        'data/ir_cron_batch_close.xml',
        'views/nuvei_module_config_views.xml',
        'views/pos_payment_method_views.xml',
        'views/pos_payment_nuvei_views.xml',
        'views/nuvei_invoice_payment_views.xml',
    ],
    # Assets del POS - carga todos los JS bajo static/src/
    'assets': {
        'point_of_sale._assets_pos': [
            'pos_cloudlion_nuvei/static/src/**/*',
        ],
    },
    'installable': True,
    'license': 'LGPL-3',
}
