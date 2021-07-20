# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import phonenumbers

from werkzeug import urls

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    mollie_card_token = fields.Char()
    mollie_payment_method = fields.Char()
    mollie_payment_issuer = fields.Char()

    def _get_specific_processing_values(self, processing_values):
        res = super()._get_specific_processing_values(processing_values)
        if self.provider != 'mollie':
            return res

        checkout_session = self._mollie_create_payment_record(processing_values)
        return {
            'publishable_key': self.acquirer_id.stripe_publishable_key,
            'session_id': checkout_session['id'],
        }

    def _mollie_create_payment_record(self, values):
        self.ensure_one()
        method_record = self.acquirer_id.mollie_methods_ids.filtered(lambda m: m.method_code == self.mollie_payment_method)

        if method_record and method_record.supports_order_api:
            result = self._mollie_create_order()

    def _mollie_create_order(self):
        order_source = False
        if self.invoice_ids:
            order_source = self.invoice_ids[0]
        elif self.sale_order_ids:
            order_source = self.sale_order_ids[0]

        if not order_source:
            return None

        order_type = 'Sale Order' if order_source._name == 'sale.order' else 'Invoice'

        payment_data = {
            'method': self.mollie_payment_method,
            'amount': {
                'currency': self.currency_id.name,
                'value': "%.2f" % (self.amount + self.fees)
            },

            'billingAddress': self._prepare_mollie_address(),
            "orderNumber": "%s (%s)" % (order_type, self.reference),
            'lines': self._mollie_get_order_lines(order_source),

            'metadata': {
                'transaction_id': self.id,
                'reference': self.reference,
                'type': order_type,
                "description": order_source.name
            },

            'locale': self.acquirer_id._mollie_user_locale(),
            'redirectUrl': self.acquirer_id._mollie_redirect_url(self.id),
        }

        # Mollie throws error with local URLs
        # webhook_url = self._mollie_webhook_url(self.id)
        # if "://localhost" not in webhook_url and "://192.168." not in webhook_url:
        #     payment_data['webhookUrl'] = webhook_url

        # Add if transection has cardToken
        if self.mollie_card_token:
            payment_data['payment'] = {'cardToken': self.mollie_card_token}

        # Add if transection has issuer
        if self.mollie_payment_issuer:
            payment_data['payment'] = {'issuer': self.mollie_payment_issuer}

        # result = self._mollie_make_request(payment_data)
        result = self.acquirer_id._mollie_make_request('/orders', data=payment_data, method="POST")

        # We are setting acquirer reference as we are receiving it before 3DS payment
        # So we can identify transaction with mollie respose
        if result and result.get('id'):
            self.acquirer_reference = result.get('id')
        return result

    def _prepare_mollie_address(self):
        self.ensure_one()
        result = {}
        partner = self.partner_id
        if not partner:
            return result

        # Build the name becasue 'givenName' and 'familyName' is required.
        # So we will repeat the name is one is not present
        name_parts = partner.name.split(" ")
        result['givenName'] = name_parts[0]
        result['familyName'] = ' '.join(name_parts[1:]) if len(name_parts) > 1 else result['givenName']

        # Phone
        phone = self._mollie_phone_format(self.partner_phone)
        if phone:
            result['phone'] = phone
        result['email'] = self.partner_email

        # Address
        result["streetAndNumber"] = self.partner_address or ' '
        result["postalCode"] = self.partner_zip or ' '
        result["city"] = self.partner_city or ' '
        result["country"] = self.partner_country_id and self.partner_country_id.code
        return result

    @api.model
    def _mollie_phone_format(self, phone):
        """ Only E164 phone number is allowed in mollie """
        phone = False
        if phone:
            try:
                parse_phone = phonenumbers.parse(self.phone, None)
                if parse_phone:
                    phone = phonenumbers.format_number(
                        parse_phone, phonenumbers.PhoneNumberFormat.E164
                    )
            except Exception:
                _logger.warning("Can not format customer phone number for mollie")
        return phone

    def _mollie_get_order_lines(self, order):
        lines = []
        if order._name == "sale.order":
            order_lines = order.order_line.filtered(lambda l: not l.display_type)  # ignore notes and section lines
            lines = self._mollie_prepare_so_lines(order_lines)
        if order._name == "account.move":
            order_lines = order.invoice_line_ids.filtered(lambda l: not l.display_type)  # ignore notes and section lines
            lines = self._mollie_prepare_invoice_lines(order_lines)
        return lines

    def _mollie_prepare_so_lines(self, lines):
        result = []
        for line in lines:
            line_data = self._mollie_prepare_lines_common(line)
            line_data.update({
                'quantity': int(line.product_uom_qty),    # TODO: Mollie does not support float. Test with float amount
                'unitPrice': {
                    'currency': line.currency_id.name,
                    'value': "%.2f" % line.price_reduce_taxinc
                },
                'totalAmount': {
                    'currency': line.currency_id.name,
                    'value': "%.2f" % line.price_total,
                },
                'vatRate': "%.2f" % sum(line.tax_id.mapped('amount')),
                'vatAmount': {
                    'currency': line.currency_id.name,
                    'value': "%.2f" % line.price_tax,
                }
            })
            result.append(line_data)
        return result

    def _mollie_prepare_invoice_lines(self, lines):
        """
            Note: Line pricing calculation
            Mollie need 1 unit price with tax included (with discount if any).
            Sale order line we have field for tax included/excluded unit price. But
            Invoice does not have such fields so we need to compute it manually with
            given calculation.

            Mollie needed fields and calculation (Descount is applied all unit price)
            unitPrice: tax included price for single unit
                unitPrice = total_price_tax_included / qty
                totalAmount = total_price_tax_included
                vatRate = total of tax percentage
                vatAmount = total_price_tax_included - total_price_tax_excluded
        """
        result = []
        for line in lines:
            line_data = self._mollie_prepare_lines_common(line)
            line_data.update({
                'quantity': int(line.quantity),    # TODO: Mollie does not support float. Test with float amount
                'unitPrice': {
                    'currency': line.currency_id.name,
                    'value': "%.2f" % (line.price_total / int(line.quantity))
                },
                'totalAmount': {
                    'currency': line.currency_id.name,
                    'value': "%.2f" % line.price_total,
                },
                'vatRate': "%.2f" % sum(line.tax_ids.mapped('amount')),
                'vatAmount': {
                    'currency': line.currency_id.name,
                    'value': "%.2f" % (line.price_total - line.price_subtotal),
                }
            })
            result.append(line_data)
        return result

    def _mollie_prepare_lines_common(self, line):

        product_data = {
            'name': line.name,
            "type": "physical",
        }

        if line.product_id.type == 'service':
            product_data['type'] = 'digital'  # We are considering service product as digital as we don't do shipping for it.

        if 'is_delivery' in line._fields and line.is_delivery:
            product_data['type'] = 'shipping_fee'

        if line.product_id and 'website_url' in line.product_id._fields:
            base_url = self.get_base_url()
            product_data['productUrl'] = urls.url_join(base_url, line.product_id.website_url)

        # Metadata - used to sync delivery data with shipment API
        product_data['metadata'] = {
            'line_id': line.id,
            'product_id': line.product_id.id
        }

        return product_data
