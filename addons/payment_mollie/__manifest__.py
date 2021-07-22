# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

{
    'name': 'Mollie Payment Acquirer',
    'version': '1.0',
    'category': 'Accounting/Payment Acquirers',
    'summary': 'Payment Acquirer: Mollie Implementation',
    'description': """Mollie Payment Acquirer""",

    'author': 'Odoo S.A, Applix BV, Droggol Infotech Pvt. Ltd.',
    'website': 'http://www.mollie.com',
    'license': 'LGPL-3',

    'depends': ['payment'],
    'data': [
        'views/payment_mollie_templates.xml',
        'views/payment_views.xml',
        'data/payment_acquirer_data.xml',
    ],
    'application': True
}
