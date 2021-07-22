# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from freezegun import freeze_time

from odoo.tests import tagged

from .common import MollieCommon


@tagged('post_install', '-at_install')
class MollieTest(MollieCommon):

    def test_payment_payload_with_method(self):
        tx = self.create_transaction(flow='redirect')

        payload = tx._mollie_prepare_payment_payload()

        self.assertDictEqual(payload['amount'], {'currency': 'EUR', 'value': '1111.11'})
        self.assertDictEqual(payload['metadata'], {'transaction_id': tx.id, 'reference': tx.reference})
        self.assertEqual(payload['description'], tx.reference)

    @freeze_time('2019-04-19 12:05:19')  # Freeze time for consistent singularization behavior
    def test_reference_is_singularized(self):
        """ Test singularization of reference prefixes. """
        reference = self.env['payment.transaction']._compute_reference(self.mollie.provider)
        self.assertEqual(reference, 'tx-20190419120519', "transaction reference was not correctly singularized")
