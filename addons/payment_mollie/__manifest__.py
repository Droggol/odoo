# -*- coding: utf-8 -*-

{
    'name': 'Mollie Payment Acquirer',
    'version': '1.0',
    'category': 'Accounting/Payment Acquirers',
    'summary': 'Payment Acquirer: Mollie Implementation',
    'description': """Mollie Payment Acquirer""",

    'author': 'Mollie',
    'website': 'http://www.mollie.com',

    'depends': ['payment'],
    'data': [
        'views/payment_views.xml',
        'data/payment_acquirer_data.xml',
        'security/ir.model.access.csv'
    ],
    'application': True,
}
