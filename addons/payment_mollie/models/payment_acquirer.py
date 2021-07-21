# -*- coding: utf-8 -*-

import json
import base64
import logging
import requests
from werkzeug import urls
from mollie.api.client import Client as MollieClient
from mollie.api.error import UnprocessableEntityError

from odoo import _, api, fields, models, service
from odoo.exceptions import ValidationError
from odoo.http import request

from odoo.addons.payment_mollie.controllers.main import MollieController

_logger = logging.getLogger(__name__)


class PaymentAcquirerMollie(models.Model):
    _inherit = 'payment.acquirer'

    provider = fields.Selection(
        selection_add=[('mollie', 'Mollie')], ondelete={'mollie': 'set default'})
    mollie_api_key_test = fields.Char(string="Mollie Test API key", required_if_provider="mollie", groups="base.group_user")
    mollie_api_key_prod = fields.Char(string="Mollie Live API key", required_if_provider="mollie", groups="base.group_user")
    mollie_profile_id = fields.Char("Mollie Profile ID", groups="base.group_user")
    mollie_methods_ids = fields.One2many('mollie.payment.method', 'parent_id', string='Mollie Payment Methods')

    def _get_custom_create_values(self, values):
        res = super()._get_custom_create_values(values)
        if self.provider != 'mollie':
            return res
        return {
            'mollie_card_token': values.get('mollie_token'),
            'mollie_payment_method': values.get('mollie_method'),
            'mollie_payment_issuer': values.get('mollie_issuer')
        }

    def _get_default_payment_method(self):
        self.ensure_one()
        if self.provider != 'mollie':
            return super()._get_default_payment_method()
        return self.env.ref('payment_mollie.payment_method_mollie').id

    def action_mollie_sync_methods(self):
        methods = self._api_mollie_get_active_payment_methods()
        if methods:
            self._sync_mollie_methods(methods)
            self._create_method_translations()

    def _sync_mollie_methods(self, methods_data):
        """ Create/Update the mollie payment methods based on configuration in
        the mollie.com.

        :param dict methods_data: enabled method's data from mollie
        """

        # Activate/Deactivate existing methods
        existing_methods = self.with_context(active_test=False).mollie_methods_ids
        for method in existing_methods:
            method.active = method.method_code in methods_data.keys()

        # Create New methods
        MolliePaymentMethod = self.env['mollie.payment.method']
        methods_to_create = methods_data.keys() - set(existing_methods.mapped('method_code'))
        for method in methods_to_create:
            method_info = methods_data[method]
            create_vals = {
                'name': method_info['description'],
                'method_code': method_info['id'],
                'parent_id': self.id,
                'supports_order_api': method_info.get('support_order_api', False),
                'supports_payment_api': method_info.get('support_payment_api', False)
            }

            # Manage issuer for the method
            if method_info.get('issuers'):
                issuer_ids = []
                for issuer_data in method_info['issuers']:
                    MollieIssuer = self.env['mollie.payment.method.issuer']
                    issuer = MollieIssuer.search([('issuers_code', '=', issuer_data['id'])], limit=1)
                    if not issuer:
                        issuer_create_vals = {
                            'name': issuer_data['name'],
                            'issuers_code': issuer_data['id'],
                        }
                        icon = self.env['payment.icon'].search([('name', '=', issuer_data['name'])], limit=1)
                        image_url = issuer_data.get('image', {}).get('size2x')
                        if not icon and image_url:
                            icon = self.env['payment.icon'].create({
                                'name': issuer_data['name'],
                                'image': self._mollie_fetch_image_by_url(image_url)
                            })
                        issuer_create_vals['payment_icon_ids'] = [(6, 0, [icon.id])]
                        issuer = MollieIssuer.create(issuer_create_vals)
                    issuer_ids.append(issuer.id)
                if issuer_ids:
                    create_vals['payment_issuer_ids'] = [(6, 0, issuer_ids)]

            # Manage icons for methods
            icon = self.env['payment.icon'].search([('name', '=', method_info['description'])], limit=1)
            image_url = method_info.get('image', {}).get('size2x')
            if not icon and image_url:
                icon = self.env['payment.icon'].create({
                    'name': method_info['description'],
                    'image': self._mollie_fetch_image_by_url(image_url)
                })
            if icon:
                create_vals['payment_icon_ids'] = [(6, 0, [icon.id])]
            MolliePaymentMethod.create(create_vals)

    def _create_method_translations(self):
        """ This method add translated terms for the method names.
        These translations are provided by mollie locale.
        """
        IrTranslation = self.env['ir.translation']
        supported_locale = self._mollie_get_supported_locale()
        supported_locale.remove('en_US')  # en_US is default
        active_langs = self.env['res.lang'].search([('code', 'in', supported_locale)])
        mollie_methods = self.mollie_methods_ids

        for lang in active_langs:
            existing_trans = self.env['ir.translation'].search([('name', '=', 'mollie.payment.method,name'), ('lang', '=', lang.code)])
            translated_method_ids = existing_trans.mapped('res_id')
            method_to_translate = []
            for method in mollie_methods:
                if method.id not in translated_method_ids:
                    method_to_translate.append(method.id)

            # This will avoid unnessesorry network calls
            if method_to_translate:
                methods_data = self._api_mollie_get_active_payment_methods(extra_params={'locale': lang.code})
                for method_id in method_to_translate:
                    mollie_method = mollie_methods.filtered(lambda m: m.id == method_id)
                    translated_value = methods_data.get(mollie_method.method_code, {}).get('description')
                    if translated_value:
                        IrTranslation.create({
                            'type': 'model',
                            'name': 'mollie.payment.method,name',
                            'lang': lang.code,
                            'res_id': method_id,
                            'src': mollie_method.name,
                            'value': translated_value,
                            'state': 'translated',
                        })

    def mollie_get_active_methods(self, order=None):
        methods = self.mollie_methods_ids.filtered(lambda m: m.active and m.active_on_shop)

        if not self.sudo().mollie_profile_id:
            methods = methods.filtered(lambda m: m.method_code != 'creditcard')

        extra_params = {}
        if order and order._name == 'sale.order':
            extra_params['amount'] = {'value': "%.2f" % order.amount_total, 'currency': order.currency_id.name}
            if order.partner_invoice_id.country_id:
                extra_params['billingCountry'] = order.partner_invoice_id.country_id.code
        if order and order._name == 'account.move':
            extra_params['amount'] = {'value': "%.2f" % order.amount_residual, 'currency': order.currency_id.name}
            if order.partner_id.country_id:
                extra_params['billingCountry'] = order.partner_id.country_id.code

        # Hide only order type methods from transection links
        if request and request.httprequest.path == '/website_payment/pay':
            methods = methods.filtered(lambda m: m.supports_payment_api)

        # Hide based on country
        if request:
            country_code = request.session.geoip and request.session.geoip.get('country_code') or False
            if country_code:
                methods = methods.filtered(lambda m: not m.country_ids or country_code in m.country_ids.mapped('code'))

        # Hide methods if mollie does not supports them
        suppported_methods = self.sudo()._api_mollie_get_active_payment_methods(extra_params=extra_params)   # sudo as public user do not have access
        methods = methods.filtered(lambda m: m.method_code in suppported_methods.keys())

        return methods

    def mollie_form_generate_values(self, tx_values):
        self.ensure_one()
        tx_reference = tx_values.get('reference')
        if not tx_reference:
            error_msg = _('Mollie: received data with missing tx reference (%s)') % (tx_reference)
            _logger.info(error_msg)
            raise ValidationError(error_msg)

        transaction = self.env['payment.transaction'].sudo().search([('reference', '=', tx_reference)])
        base_url = self.get_base_url()
        tx_values['base_url'] = base_url
        tx_values['checkout_url'] = False
        tx_values['error_msg'] = False
        tx_values['status'] = False

        if transaction:

            result = None

            # check if order api is supportable by selected mehtod
            method_record = self._mollie_get_method_record(transaction.mollie_payment_method)
            if method_record.supports_order_api:
                result = self._mollie_create_order(transaction)

            # Fallback to payment method
            # Case: When invoice is partially paid or partner have credit note
            # then mollie can not create order because orderline and total amount is diffrent
            # in that case we have fall back on payment method.
            if (result and result.get('error') or result is None) and method_record.supports_payment_api:
                if result and result.get('error'):
                    _logger.warning("Can not use order api due to '%s' fallback on payment" % (result.get('error')))
                result = self._mollie_create_payment(transaction)

            if result.get('error'):
                tx_values['error_msg'] = result['error']
                self.env.cr.rollback()    # Roll back if there is error
                return tx_values

            if result.get('status') == 'paid':
                transaction.form_feedback(result, "mollie")
            else:
                tx_values['checkout_url'] = result["_links"]["checkout"]["href"]
            tx_values['status'] = result.get('status')
        return tx_values

    def mollie_get_form_action_url(self):
        return "/payment/mollie/action"

    def _mollie_create_order(self, transaction):
        order_source = False
        if transaction.invoice_ids:
            order_source = transaction.invoice_ids[0]
        elif transaction.sale_order_ids:
            order_source = transaction.sale_order_ids[0]

        if not order_source:
            return None

        order_type = 'Sale Order' if order_source._name == 'sale.order' else 'Invoice'

        payment_data = {
            'method': transaction.mollie_payment_method,
            'amount': {
                'currency': transaction.currency_id.name,
                'value': "%.2f" % (transaction.amount + transaction.fees)
            },

            'billingAddress': order_source.partner_id._prepare_mollie_address(),
            "orderNumber": "%s (%s)" % (order_type, transaction.reference),
            'lines': self._mollie_get_order_lines(order_source, transaction),

            'metadata': {
                'transaction_id': transaction.id,
                'reference': transaction.reference,
                'type': order_type,

                # V12 fallback
                "order_id": "ODOO-%s" % (transaction.reference),
                "description": order_source.name
            },

            'locale': self._mollie_user_locale(),
            'redirectUrl': self._mollie_redirect_url(transaction.id),
        }

        # Mollie throws error with local URL
        webhook_url = self._mollie_webhook_url(transaction.id)
        if "://localhost" not in webhook_url and "://192.168." not in webhook_url:
            payment_data['webhookUrl'] = webhook_url

        # Add if transection has cardToken
        if transaction.mollie_payment_token:
            payment_data['payment'] = {'cardToken': transaction.mollie_payment_token}

        # Add if transection has issuer
        if transaction.mollie_payment_issuer:
            payment_data['payment'] = {'issuer': transaction.mollie_payment_issuer}

        result = self._api_mollie_create_order(payment_data)

        # We are setting acquirer reference as we are receiving it before 3DS payment
        # So we can identify transaction with mollie respose
        if result and result.get('id'):
            transaction.acquirer_reference = result.get('id')
        return result

    def _mollie_create_payment(self, transaction):
        """ This method is used as fallback. When order method fails. """
        payment_data = {
            'method': transaction.mollie_payment_method,
            'amount': {
                'currency': transaction.currency_id.name,
                'value': "%.2f" % (transaction.amount + transaction.fees)
            },
            'description': transaction.reference,

            'metadata': {
                'transaction_id': transaction.id,
                'reference': transaction.reference,
            },

            'locale': self._mollie_user_locale(),
            'redirectUrl': self._mollie_redirect_url(transaction.id),
        }

        # Mollie throws error with local URL
        webhook_url = self._mollie_webhook_url(transaction.id)
        if "://localhost" not in webhook_url and "://192.168." not in webhook_url:
            payment_data['webhookUrl'] = webhook_url

        # Add if transection has cardToken
        if transaction.mollie_payment_token:
            payment_data['cardToken'] = transaction.mollie_payment_token

        # Add if transection has issuer
        if transaction.mollie_payment_issuer:
            payment_data['issuer'] = transaction.mollie_payment_issuer

        result = self._api_mollie_create_payment(payment_data)

        # We are setting acquirer reference as we are receiving it before 3DS payment
        # So we can identify transaction with mollie respose
        if result and result.get('id'):
            transaction.acquirer_reference = result.get('id')
        return result

    def _mollie_get_payment_data(self, transection_reference):
        """ Sending force_payment=True will send payment data even if transection_reference is for order api """
        mollie_data = False
        if transection_reference.startswith('ord_'):
            mollie_data = self._mollie_make_request(f'/orders/{transection_reference}', params={'embed': 'payments'}, method="GET")
        if transection_reference.startswith('tr_'):    # This is not used
            mollie_data = self._mollie_make_request(f'/payments/{transection_reference}', method="GET")
        return mollie_data

    # -----------------------------------------------
    # API methods that uses to mollie python lib
    # -----------------------------------------------

    def _mollie_make_request(self, endpoint, params=None, data=None, method='POST'):
        """ Make a request at mollie endpoint

        Note: self.ensure_one()

        :param str endpoint: The endpoint to be reached by the request
        :param dict params: The querystring of the request
        :param dict data: The pyload of the request
        :param str method: The HTTP method of the request
        :return The JSON-formatted content of the response
        :rtype: dict
        :raise: ValidationError if an HTTP error occurs
        """
        self.ensure_one()
        endpoint = f'/v2/{endpoint.strip("/")}'
        url = urls.url_join('https://api.mollie.com/', endpoint)
        mollie_api_key = self.mollie_api_key_prod if self.state == 'enabled' else self.mollie_api_key_test
        params = self._mollie_generate_querystring(params)

        # User agent strings used by mollie to find issues in integration
        odoo_version = service.common.exp_version()['server_version']
        mollie_version = self.env.ref('base.module_payment_mollie').installed_version

        headers = {
            "Accept": "application/json",
            "Authorization": f'Bearer {mollie_api_key}',
            "Content-Type": "application/json",
            "User-Agent": f'Odoo/{odoo_version} MollieOdoo/{mollie_version}',
        }
        if data:
            data = json.dumps(data)
        try:
            response = requests.request(method, url, params=params, data=data, headers=headers, timeout=(10, 20))
            response.raise_for_status()
        except requests.exceptions.ConnectionError:
            _logger.exception("Unable to communicate with Mollie: %s", url)
            raise ValidationError("Mollie: " + _("Could not establish the connection to the API."))
        return response.json()

    def _api_mollie_get_active_payment_methods(self, extra_params={}):
        """ Get method data from the mollie. It will return the methods
        that are enabled in the Mollie.

        :param dict extra_params: Optional parameters which are passed to mollie during API call
        :return: details of enabled methods
        :rtype: dict
        """
        result = {}
        params = {'include': 'issuers', 'includeWallets': 'applepay', **extra_params}

        # get payment api methods
        payemnt_api_methods = self._mollie_make_request('/methods', params=params, method="GET")
        if payemnt_api_methods.get('count'):
            for method in payemnt_api_methods['_embedded']['methods']:
                method['support_payment_api'] = True
                result[method['id']] = method

        # get order api methods
        params['resource'] = 'orders'
        order_api_methods = self._mollie_make_request('/methods', params=params, method="GET")
        if order_api_methods.get('count'):
            for method in order_api_methods['_embedded']['methods']:
                if method['id'] in result:
                    result[method['id']]['support_order_api'] = True
                else:
                    method['support_order_api'] = True
                    result[method['id']] = method
        return result

    def _api_mollie_get_client(self):
        """ Creates the mollie client object based. It will be used to make
        diffrent API calls to mollie.

        :return: mollie clint object
        :rtype: MollieClient
        """
        mollie_client = MollieClient(timeout=5)
        if self.state == 'enabled':
            mollie_client.set_api_key(self.mollie_api_key_prod)
        elif self.state == 'test':
            mollie_client.set_api_key(self.mollie_api_key_test)

        mollie_client.set_user_agent_component('Odoo', service.common.exp_version()['server_version'])
        mollie_client.set_user_agent_component('MollieOdoo', self.env.ref('base.module_payment_mollie').installed_version)
        return mollie_client

    def _api_mollie_create_payment(self, payment_data):
        mollie_client = self._api_mollie_get_client()
        try:
            result = mollie_client.payments.create(payment_data)
        except UnprocessableEntityError as e:
            return {'error': str(e)}
        return result

    def _api_mollie_create_order(self, payment_data):
        mollie_client = self._api_mollie_get_client()
        try:
            result = mollie_client.orders.create(payment_data)
        except UnprocessableEntityError as e:
            return {'error': str(e)}
        return result

    def _api_mollie_get_payment(self, tx_id):
        mollie_client = self._api_mollie_get_client()
        return mollie_client.payments.get(tx_id)

    def _api_mollie_get_order(self, tx_id):
        mollie_client = self._api_mollie_get_client()
        return mollie_client.orders.get(tx_id, embed="payments")

    def _api_mollie_refund(self, amount, currency, payment_record):
        mollie_client = self._api_mollie_get_client()
        refund = mollie_client.payment_refunds.on(payment_record).create({
            'amount': {
                'value': "%.2f" % amount,
                'currency': currency.name
            }
        })
        return refund

    # -----------------------------------------------
    # Methods that create mollie order payload
    # -----------------------------------------------

    def _mollie_get_order_lines(self, order, transaction):
        lines = []
        if order._name == "sale.order":
            order_lines = order.order_line.filtered(lambda l: not l.display_type)  # ignore notes and section lines
            lines = self._mollie_prepare_so_lines(order_lines, transaction)
        if order._name == "account.move":
            order_lines = order.invoice_line_ids.filtered(lambda l: not l.display_type)  # ignore notes and section lines
            lines = self._mollie_prepare_invoice_lines(order_lines, transaction)
        if transaction.fees:    # Fees or Surcharge (if configured)
            fees_line = self._mollie_prepare_fees_line(transaction)
            lines.append(fees_line)
        return lines

    def _mollie_prepare_fees_line(self, transaction):
        return {
            'name': _('Acquirer Fees'),
            'type': 'surcharge',
            'metadata': {
                "type": 'surcharge'
            },
            'quantity': 1,
            'unitPrice': {
                'currency': transaction.currency_id.name,
                'value': "%.2f" % transaction.fees
            },
            'totalAmount': {
                'currency': transaction.currency_id.name,
                'value': "%.2f" % transaction.fees
            },
            'vatRate': 0,
            'vatAmount': {
                'currency': transaction.currency_id.name,
                'value': 0,
            }
        }

    def _mollie_prepare_so_lines(self, lines, transaction):
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

            if transaction.mollie_payment_method == 'voucher':
                category = line.product_template_id._get_mollie_voucher_category()
                if category:
                    line_data.update({
                        'category': category
                    })

            result.append(line_data)
        return result

    def _mollie_prepare_invoice_lines(self, lines, transaction):
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

            if transaction.mollie_payment_method == 'voucher':
                category = line.product_id.product_tmpl_id._get_mollie_voucher_category()
                if category:
                    line_data.update({
                        'category': category
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

    # -----------------------------------------------
    # Helper methods for mollie
    # -----------------------------------------------

    def _mollie_user_locale(self):
        user_lang = self.env.context.get('lang')
        supported_locale = self._mollie_get_supported_locale()
        return user_lang if user_lang in supported_locale else 'en_US'

    def _mollie_get_supported_locale(self):
        return [
            'en_US', 'nl_NL', 'nl_BE', 'fr_FR',
            'fr_BE', 'de_DE', 'de_AT', 'de_CH',
            'es_ES', 'ca_ES', 'pt_PT', 'it_IT',
            'nb_NO', 'sv_SE', 'fi_FI', 'da_DK',
            'is_IS', 'hu_HU', 'pl_PL', 'lv_LV',
            'lt_LT']

    def _mollie_webhook_url(self, tx_id):
        base_url = self.get_base_url()
        redirect_url = urls.url_join(base_url, MollieController._notify_url)
        return "%s?tx=%s" % (redirect_url, tx_id)

    def _mollie_get_method_record(self, method_code):
        return self.env['mollie.payment.method'].search([('method_code', '=', method_code)], limit=1)

    def _mollie_fetch_image_by_url(self, image_url):
        image_base64 = False
        try:
            image_base64 = base64.b64encode(requests.get(image_url).content)
        except Exception:
            _logger.warning('Can not import mollie image %s' % image_url)
        return image_base64

    def _mollie_generate_querystring(self, params):
        """ Mollie uses dictionaries in querystrings with square brackets like this
            https://api.mollie.com/v2/methods?amount[value]=300.00&amount[currency]=EUR

            :param dict params: parameters which needs to be converted in mollie format
            :return: querystring in mollie's format
            :rtype: string
        """
        if not params:
            return None
        parts = []
        for param, value in sorted(params.items()):
            if not isinstance(value, dict):
                parts.append(urls.url_encode({param: value}))
            else:
                # encode dictionary with square brackets
                for key, sub_value in sorted(value.items()):
                    composed = f"{param}[{key}]"
                    parts.append(urls.url_encode({composed: sub_value}))
        if parts:
            return "&".join(parts)
