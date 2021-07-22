# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
from odoo.addons.payment.tests.common import PaymentCommon


class MollieCommon(PaymentCommon):

    @classmethod
    def setUpClass(cls, chart_template_ref=None):
        super().setUpClass(chart_template_ref=chart_template_ref)

        cls.mollie = cls._prepare_acquirer('mollie', update_values={
            'mollie_api_key_test': 'dummy',
            'mollie_api_key_prod': 'dummy',
            'mollie_profile_id': 'dummy'
        })

        cls.acquirer = cls.mollie
        cls.currency = cls.currency_euro
