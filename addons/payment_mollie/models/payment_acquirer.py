# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import json
import logging
import requests
from werkzeug import urls

from odoo import _, fields, models, service
from odoo.exceptions import ValidationError


_logger = logging.getLogger(__name__)


class PaymentAcquirerMollie(models.Model):
    _inherit = 'payment.acquirer'

    provider = fields.Selection(selection_add=[('mollie', 'Mollie')], ondelete={'mollie': 'set default'})
    mollie_api_key_test = fields.Char(string="Mollie Test API key", required_if_provider="mollie", groups="base.group_user")
    mollie_api_key_prod = fields.Char(string="Mollie Live API key", required_if_provider="mollie", groups="base.group_user")

    # ------------------
    # OVERRIDDEN METHODS
    # ------------------

    def _get_default_payment_method_id(self):
        self.ensure_one()
        if self.provider != 'mollie':
            return super()._get_default_payment_method_id()
        return self.env.ref('payment_mollie.payment_method_mollie').id

    # -----------
    # API methods
    # -----------

    def _mollie_make_request(self, endpoint, data=None, method='POST'):
        """ Make a request at mollie endpoint.

        Note: self.ensure_one()

        :param str endpoint: The endpoint to be reached by the request
        :param dict data: The payload of the request
        :param str method: The HTTP method of the request
        :return The JSON-formatted content of the response
        :rtype: dict
        :raise: ValidationError if an HTTP error occurs
        """
        self.ensure_one()
        endpoint = f'/v2/{endpoint.strip("/")}'
        url = urls.url_join('https://api.mollie.com/', endpoint)
        mollie_api_key = self.mollie_api_key_prod if self.state == 'enabled' else self.mollie_api_key_test

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
            response = requests.request(method, url, data=data, headers=headers, timeout=60)
            response.raise_for_status()
        except requests.exceptions.RequestException:
            _logger.exception("Unable to communicate with Mollie: %s", url)
            raise ValidationError("Mollie: " + _("Could not establish the connection to the API."))
        return response.json()

    def _api_mollie_create_payment_record(self, payment_data):
        """ Create the payment records on the mollie. It calls payment or order
        API based on 'api_type' param.

        :param str api_type: api is selected based on this parameter
        :param dict payment_data: payment data
        :return: details of created payment record
        :rtype: dict
        """
        return self._mollie_make_request('/payments', data=payment_data, method="POST")

    def _api_mollie_get_payment_data(self, transaction_reference):
        """ Fetch the payment records based `transaction_reference`. It is used
        to varify transaction's state after the payment.

        :param str transaction_reference: transaction reference
        :return: details of payment record
        :rtype: dict
        """
        return self._mollie_make_request(f'/payments/{transaction_reference}', method="GET")
