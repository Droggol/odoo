# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from freezegun import freeze_time

from odoo.tests import tagged

from .common import MollieCommon


@tagged('post_install', '-at_install')
class MollieTest(MollieCommon):

    def test_payment_payload_with_method(self):
        tx = self.create_transaction(flow='direct', mollie_payment_method='paypal')

        payload = tx._mollie_prepare_payment_payload('payment')

        self.assertEqual(payload['method'], 'paypal')
        self.assertDictEqual(payload['amount'], {'currency': 'EUR', 'value': '1111.11'})
        self.assertDictEqual(payload['metadata'], {'transaction_id': tx.id, 'reference': tx.reference})
        self.assertEqual(payload['description'], tx.reference)

    def test_payment_payload_with_card_token(self):
        tx = self.create_transaction(flow='direct', mollie_payment_method='creditcard', mollie_card_token="dr_testtoken")

        payload = tx._mollie_prepare_payment_payload('payment')

        self.assertEqual(payload['method'], 'creditcard')
        self.assertDictEqual(payload['payment'], {'cardToken': 'dr_testtoken'})

    def test_payment_payload_with_issuer(self):
        tx = self.create_transaction(flow='direct', mollie_payment_method='ideal', mollie_payment_issuer="ideal_ABNANL2A")

        payload = tx._mollie_prepare_payment_payload('payment')

        self.assertEqual(payload['method'], 'ideal')
        self.assertDictEqual(payload['payment'], {'issuer': "ideal_ABNANL2A"})

    @freeze_time('2019-04-19 12:05:19')  # Freeze time for consistent singularization behavior
    def test_reference_is_singularized(self):
        """ Test singularization of reference prefixes. """
        reference = self.env['payment.transaction']._compute_reference(self.mollie.provider)
        self.assertEqual(reference, 'tx-20190419120519', "transaction reference was not correctly singularized")

    def test_payment_address_payload(self):
        expected_data = {
            'givenName': 'Parth',
            'familyName': 'Gajjar',
            'email': 'test@example.com',
            'streetAndNumber': 'Street 1',
            'postalCode': '124421',
            'city': 'My City',
            'country': 'IN'
        }

        partner_record = self.env['res.partner'].create({
            'name': 'Parth Gajjar',
            'street': 'Street 1',
            'zip': '124421',
            'phone': '+91 9999999999',
            'email': 'test@example.com',
            'city': 'My City',
            'country_id': self.env.ref('base.in').id,
        })

        tx = self.create_transaction(flow='direct', mollie_payment_method='paypal', partner_id=partner_record.id)
        address_data = tx._prepare_mollie_address()
        self.assertDictEqual(address_data, expected_data)
