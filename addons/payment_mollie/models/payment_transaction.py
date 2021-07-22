# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import phonenumbers

from werkzeug import urls

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.addons.payment import utils as payment_utils
from odoo.addons.payment_mollie.controllers.main import MollieController

_logger = logging.getLogger(__name__)


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    mollie_card_token = fields.Char()
    mollie_payment_method = fields.Char()
    mollie_payment_issuer = fields.Char()

    @api.model
    def _compute_reference(self, provider, prefix=None, separator='-', **kwargs):
        if provider == 'mollie':
            if not prefix:
                prefix = self.sudo()._compute_reference_prefix(
                    provider, separator, **kwargs
                ) or None
            prefix = payment_utils.singularize_reference_prefix(prefix=prefix, separator=separator)
        return super()._compute_reference(provider, prefix=prefix, separator=separator, **kwargs)

    def _get_specific_processing_values(self, processing_values):
        res = super()._get_specific_processing_values(processing_values)
        if self.provider != 'mollie':
            return res

        payment_data = self._create_mollie_record_from_transection()

        if payment_data["_links"].get("checkout"):
            redirect_url = payment_data["_links"]["checkout"]["href"]
        else:
            redirect_url = payment_data.get('redirectUrl')

        return {
            'status': payment_data.get('status'),
            'redirect_url': redirect_url
        }

    def _get_tx_from_feedback_data(self, provider, data):
        """ Override of payment to find the transaction based on Mollie data.

        :param str provider: The provider of the acquirer that handled the transaction
        :param dict data: The feedback data sent by the provider
        :return: The transaction if found
        :rtype: recordset of `payment.transaction`
        :raise: ValidationError if the data match no transaction
        """
        tx = super()._get_tx_from_feedback_data(provider, data)
        if provider != 'mollie':
            return tx

        tx = self.search([('reference', '=', data.get('ref')), ('provider', '=', 'mollie')])
        if not tx:
            raise ValidationError(
                "Mollie: " + _("No transaction found matching reference %s.", data.get('ref'))
            )
        return tx

    def _process_feedback_data(self, data):
        """ Override of payment to process the transaction based on Mollie data.

        Note: self.ensure_one()

        :param dict data: The feedback data sent by the provider
        :return: None
        """
        super()._process_feedback_data(data)
        if self.provider != 'mollie':
            return

        if self.state == 'done':
            return

        acquirer_reference = self.acquirer_reference
        mollie_payment = self.acquirer_id._api_mollie_get_payment_data(acquirer_reference)
        payment_status = mollie_payment.get('status')
        if payment_status == 'paid':
            self._set_done()
        elif payment_status == 'pending':
            self._set_pending()
        elif payment_status == 'authorized':
            self._set_authorized()
        elif payment_status in ['expired', 'canceled', 'failed']:
            self._set_canceled("Mollie: " + _("Mollie: canceled due to status: %s", payment_status))
        else:
            _logger.info("received data with invalid payment status: %s", payment_status)
            self._set_error(
                "Mollie: " + _("Received data with invalid payment status: %s", payment_status)
            )

    def _create_mollie_record_from_transection(self):
        """ In order to capture payment from mollie we need to create a record on mollie.

        Mollie have 2 type of api to create payment record,
         * order api (used for sales orders)
         * payment api (used for invoices and other payments)

        Different methods suppports diffrent api we choose the api based on that. Also
        we have used payment api as fallback api if order api fails.

        Note: self.ensure_one()

        :return: None
        """
        self.ensure_one()
        method_record = self.acquirer_id.mollie_methods_ids.filtered(lambda m: m.method_code == self.mollie_payment_method)

        result = None
        if self.sale_order_ids and method_record.supports_order_api:
            # Order API
            result = self._mollie_create_payment_record('order')

        # Payment API
        if (result and result.get('error') or result is None) and method_record.supports_payment_api:
            if result and result.get('error'):
                _logger.warning("Can not use order api due to '%s' fallback on payment" % (result.get('error')))
            result = self._mollie_create_payment_record('payment')

        return result

    def _mollie_create_payment_record(self, api_type):
        """ This method prepare the payload based in api type and then
        creates the payment/order record in mollie based on api type.

        :param str api_type: api is selected based on this parameter
        :return: data of created record received from mollie api
        :rtype: dict
        """

        base_url = self.acquirer_id.get_base_url()
        redirect_url = urls.url_join(base_url, MollieController._return_url)

        payment_data = {
            'method': self.mollie_payment_method,
            'amount': {
                'currency': self.currency_id.name,
                'value': "%.2f" % (self.amount + self.fees)
            },
            'metadata': {
                'transaction_id': self.id,
                'reference': self.reference,
            },
            'locale': self.acquirer_id._mollie_user_locale(),
            'redirectUrl': f'{redirect_url}?ref={self.reference}'
        }

        if api_type == 'order':
            # Order api parameters
            order = self.sale_order_ids[0]
            payment_data.update({
                'billingAddress': self._prepare_mollie_address(),
                'orderNumber': f'{_("Sale Order")} ({self.reference})',
                'lines': self._mollie_get_order_lines(order),
            })
        else:
            # Payment api parameters
            payment_data['description'] = self.reference

        # Mollie rejects some local ips/URLs
        # https://help.mollie.com/hc/en-us/articles/213470409
        webhook_url = urls.url_join(base_url, MollieController._notify_url)
        if "://localhost" not in webhook_url and "://192.168." not in webhook_url and "://127." not in webhook_url:
            payment_data['webhookUrl'] = f'{webhook_url}?ref={self.reference}'

        # Add if transection has cardToken
        if self.mollie_card_token:
            payment_data['payment'] = {'cardToken': self.mollie_card_token}

        # Add if transection has issuer
        if self.mollie_payment_issuer:
            payment_data['payment'] = {'issuer': self.mollie_payment_issuer}

        result = self.acquirer_id._api_mollie_create_payment_record(api_type, payment_data)

        # We are setting acquirer reference as we are receiving it before 3DS payment
        # So we can verify the validity of the transecion
        if result and result.get('id'):
            self.acquirer_reference = result.get('id')
        return result

    def _mollie_get_order_lines(self, order):
        """ This method prepares order line data for order api

        :param order: sale.order record based on this payload will be genrated
        :return: order line data for order api
        :rtype: dict
        """
        lines = []
        for line in order.order_line:
            line_data = {
                'name': line.name,
                "type": "physical",
                'quantity': int(line.product_uom_qty),    # Mollie does not support float.
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
            }
            if line.product_id.type == 'service':
                line_data['type'] = 'digital'  # We are considering service product as digital as we don't do shipping for it.

            if 'is_delivery' in line._fields and line.is_delivery:
                line_data['type'] = 'shipping_fee'

            if line.product_id and 'website_url' in line.product_id._fields:
                base_url = self.get_base_url()
                line_data['productUrl'] = urls.url_join(base_url, line.product_id.website_url)

            line_data['metadata'] = {
                'line_id': line.id,
                'product_id': line.product_id.id
            }
            if self.mollie_payment_method == 'voucher':
                category = line.product_id.product_tmpl_id._get_mollie_voucher_category()
                if category:
                    line_data.update({
                        'category': category
                    })
            lines.append(line_data)
        return lines

    def _prepare_mollie_address(self):
        """ This method prepare address used in order api of mollie

        :return: address data for order api
        :rtype: dict
        """
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
        """ Mollie only allows E164 phone numbers so this method checks whether its validity."""
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
