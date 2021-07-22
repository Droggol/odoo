# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging

from werkzeug import urls

from odoo import _, api, models
from odoo.exceptions import ValidationError
from odoo.addons.payment import utils as payment_utils
from odoo.addons.payment_mollie.controllers.main import MollieController

_logger = logging.getLogger(__name__)


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    @api.model
    def _compute_reference(self, provider, prefix=None, separator='-', **kwargs):
        """ Override of payment to compute a unique reference for the mollie transaction.

        We need this because, after the successful payment mollie redirect back to Odoo
        based on `redirectUrl` param in payload. In this redirecation mollie does not provide
        any data to identify transection. So we create unique reference here so in the later stage
        we can identify the transection.

        See more info at `redirectUrl` param in method _mollie_prepare_payment_payload()

        :param str provider: The provider of the acquirer handling the transaction
        :param str prefix: The custom prefix used to compute the full reference
        :param str separator: The custom separator used to separate the prefix from the suffix
        :return: The unique reference for the transaction
        :rtype: str
        """
        if provider == 'mollie':
            if not prefix:
                prefix = self.sudo()._compute_reference_prefix(
                    provider, separator, **kwargs
                ) or None
            prefix = payment_utils.singularize_reference_prefix(prefix=prefix, separator=separator)
        return super()._compute_reference(provider, prefix=prefix, separator=separator, **kwargs)

    def _get_specific_rendering_values(self, processing_values):
        res = super()._get_specific_rendering_values(processing_values)
        if self.provider != 'mollie':
            return res

        api_url = False
        mollie_payload = self._mollie_prepare_payment_payload()
        payment_data = self.acquirer_id._api_mollie_create_payment_record(mollie_payload)

        if payment_data and payment_data.get('id'):
            api_url = payment_data["_links"]["checkout"]["href"]

            # We are setting acquirer reference as we are receiving it before the redirection to mollie.
            # This will help us verify the validity of the transaction later.
            self.acquirer_reference = payment_data.get('id')

        return {
            'api_url': api_url
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
            _logger.info("Received data with invalid payment status: %s", payment_status)
            self._set_error("Mollie: " + _("Received data with invalid payment status: %s", payment_status))

    def _mollie_prepare_payment_payload(self):
        """ This method prepare the payload based in api type.

        Note: this method are splitted so we can write test cases

        :return: data of created record received from mollie api
        :rtype: dict
        """
        base_url = self.acquirer_id.get_base_url()
        redirect_url = urls.url_join(base_url, MollieController._return_url)

        payment_data = {
            'description': self.reference,
            'amount': {
                'currency': self.currency_id.name,
                'value': "%.2f" % self.amount
            },
            'metadata': {
                'transaction_id': self.id,
                'reference': self.reference,
            },
            'locale': self._mollie_user_locale(),

            # We send the unique transection reference in the return url. After the payment,
            # mollie redirect back too Odoo but Mollie does not send any data so this reference
            # will help us identify transection after redirection. Without this Odoo will not be
            # able to validate the payment because in some cases webshooks are not reachable.
            'redirectUrl': f'{redirect_url}?ref={self.reference}'
        }

        # Mollie rejects some local IPs/URLs
        # https://help.mollie.com/hc/en-us/articles/213470409
        webhook_url = urls.url_join(base_url, MollieController._notify_url)
        if "://localhost" not in webhook_url and "://192.168." not in webhook_url and "://127." not in webhook_url:
            payment_data['webhookUrl'] = f'{webhook_url}?ref={self.reference}'

        return payment_data

    def _mollie_user_locale(self):
        """ This method returns the locale string based on currunt context.

        :return: locale string
        :rtype: str
        """
        user_lang = self.env.context.get('lang')
        mollie_supported_locale = [
            'en_US', 'nl_NL', 'nl_BE', 'fr_FR',
            'fr_BE', 'de_DE', 'de_AT', 'de_CH',
            'es_ES', 'ca_ES', 'pt_PT', 'it_IT',
            'nb_NO', 'sv_SE', 'fi_FI', 'da_DK',
            'is_IS', 'hu_HU', 'pl_PL', 'lv_LV',
            'lt_LT']
        return user_lang if user_lang in mollie_supported_locale else 'en_US'
