{
    'name': 'Sale Order Kit Breakdown',
    'version': '19.0.1.0.0',
    'category': 'Sales',
    'summary': 'Displays the breakdown of Kit-type products in Sales Orders',
    'author': 'CloudLion',
    'depends': ['sale_management', 'mrp'],
    'data': [
        #'security/ir.model.access.csv',
        'views/sale_order_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'sale_kit_report/static/src/scss/sale_kit_breakdown.scss',
            'sale_kit_report/static/src/js/sale_kit_breakdown.js',
            'sale_kit_report/static/src/xml/sale_kit_breakdown.xml',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}