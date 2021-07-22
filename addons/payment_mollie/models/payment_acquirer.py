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
    mollie_methods_ids = fields.One2many('mollie.payment.method', 'acquirer_id', string='Mollie Payment Methods')

    # ------------------
    # OVERRIDDEN METHODS
    # ------------------

    def _get_default_payment_method(self):
        self.ensure_one()
        if self.provider != 'mollie':
            return super()._get_default_payment_method()
        return self.env.ref('payment_mollie.payment_method_mollie').id

    def _get_custom_create_values(self, values):
        """ Override to return mollie-specific values for creation of transection.

        :param dict values: Extra values submitted from the web page
        :return: The dict of acquirer-specific create values
        :rtype: dict
        """
        res = super()._get_custom_create_values(values)
        if self.provider != 'mollie':
            return res
        return {
            'mollie_card_token': values.get('mollie_token'),
            'mollie_payment_method': values.get('mollie_method'),
            'mollie_payment_issuer': values.get('mollie_issuer')
        }

    # --------------
    # ACTION METHODS
    # --------------

    def action_mollie_sync_methods(self):
        methods = self._api_mollie_get_active_payment_methods()
        if methods:
            self._sync_mollie_methods(methods)
            self._create_method_translations()

    # ----------------
    # BUSINESS METHODS
    # ----------------

    def _sync_mollie_methods(self, methods_data):
        """ Create/Update the mollie payment methods based on configuration in the mollie.com.

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
                'acquirer_id': self.id,
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

    def _mollie_get_supported_methods(self, order, invoice, amount, currency):
        """ This method returns mollie's possible payment method based amount, currency and billing country.

        :param dict order: order record for which this transection is generated
        :return details of supported methods
        :rtype: dict
        """
        methods = self.mollie_methods_ids.filtered(lambda m: m.active and m.active_on_shop)

        if not self.sudo().mollie_profile_id:
            methods = methods.filtered(lambda m: m.method_code != 'creditcard')

        has_voucher_line, extra_params = False, {}
        if order:
            extra_params['amount'] = {'value': "%.2f" % order.amount_total, 'currency': order.currency_id.name}
            has_voucher_line = order.mapped('order_line.product_id.product_tmpl_id')._get_mollie_voucher_category()
            if order.partner_invoice_id.country_id:
                extra_params['billingCountry'] = order.partner_invoice_id.country_id.code
        else:
            # Hide the mollie methods that only supports order api
            methods = methods.filtered(lambda m: m.supports_payment_api)

        if invoice and invoice._name == 'account.move':
            extra_params['amount'] = {'value': "%.2f" % invoice.amount_residual, 'currency': invoice.currency_id.name}
            if invoice.partner_id.country_id:
                extra_params['billingCountry'] = invoice.partner_id.country_id.code

        if amount and currency:
            extra_params['amount'] = {'value': "%.2f" % amount, 'currency': currency.name}

        if not has_voucher_line:
            methods = methods.filtered(lambda m: m.method_code != 'voucher')

        # Hide based on country
        if request:
            country_code = request.session.geoip and request.session.geoip.get('country_code') or False
            if country_code:
                methods = methods.filtered(lambda m: not m.country_ids or country_code in m.country_ids.mapped('code'))

        # Hide methods if mollie does not supports them
        suppported_methods = self.sudo()._api_mollie_get_active_payment_methods(extra_params=extra_params)   # sudo as public user do not have access
        methods = methods.filtered(lambda m: m.method_code in suppported_methods.keys())

        return methods

    def _mollie_get_payment_data(self, transection_reference):
        """ Sending force_payment=True will send payment data even if transection_reference is for order api """
        mollie_data = False
        if transection_reference.startswith('ord_'):
            mollie_data = self._mollie_make_request(f'/orders/{transection_reference}', params={'embed': 'payments'}, method="GET")
        if transection_reference.startswith('tr_'):    # This is not used
            mollie_data = self._mollie_make_request(f'/payments/{transection_reference}', method="GET")
        return mollie_data

    # -----------
    # API methods
    # -----------

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

    # -------------------------
    # Helper methods for mollie
    # -------------------------

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

    def _mollie_fetch_image_by_url(self, image_url):
        image_base64 = False
        try:
            image_base64 = base64.b64encode(requests.get(image_url).content)
        except Exception:
            _logger.warning('Can not import mollie image %s' % image_url)
        return image_base64

    def _mollie_generate_querystring(self, params):
        """ Mollie uses dictionaries in querystrings with square brackets like this
            https://api.mollie.com/v2/methods?amount[value]=125.91&amount[currency]=EUR

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
