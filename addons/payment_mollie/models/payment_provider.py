# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging

import requests
from werkzeug import urls

from odoo import _, fields, models, service
from odoo.exceptions import ValidationError

from odoo.addons.payment_mollie.const import SUPPORTED_CURRENCIES

_logger = logging.getLogger(__name__)


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('mollie', 'Mollie')], ondelete={'mollie': 'set default'}
    )
    mollie_api_key = fields.Char(
        string="Mollie API Key",
        help="The Test or Live API Key depending on the configuration of the provider",
        required_if_provider="mollie", groups="base.group_system"
    )

    #=== COMPUTE METHODS ===#

    def _compute_feature_support_fields(self):
        """ Override of `payment` to enable additional features. """
        super()._compute_feature_support_fields()
        self.filtered(lambda acq: acq.provider == 'mollie').update({
            'support_refund': 'partial',
        })

    #=== BUSINESS METHODS ===#

    def _get_supported_currencies(self):
        """ Override of `payment` to return the supported currencies. """
        supported_currencies = super()._get_supported_currencies()
        if self.code == 'mollie':
            supported_currencies = supported_currencies.filtered(
                lambda c: c.name in SUPPORTED_CURRENCIES
            )
        return supported_currencies

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

        odoo_version = service.common.exp_version()['server_version']
        module_version = self.env.ref('base.module_payment_mollie').installed_version
        headers = {
            "Accept": "application/json",
            "Authorization": f'Bearer {self.mollie_api_key}',
            "Content-Type": "application/json",
            # See https://docs.mollie.com/integration-partners/user-agent-strings
            "User-Agent": f'Odoo/{odoo_version} MollieNativeOdoo/{module_version}',
        }

        try:
            response = requests.request(method, url, json=data, headers=headers, timeout=60)
            response.raise_for_status()
        except requests.exceptions.RequestException:
            _logger.exception("unable to communicate with Mollie: %s", url)
            raise ValidationError("Mollie: " + _("Could not establish the connection to the API."))
        return response.json()
