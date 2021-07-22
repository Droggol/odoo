# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import pprint

from odoo import http
from odoo.http import request, Response

_logger = logging.getLogger(__name__)


class MollieController(http.Controller):
    _return_url = "/payment/mollie/return"
    _notify_url = "/payment/mollie/notify"

    @http.route(_return_url, type='http', auth='public', methods=['GET', 'POST'], csrf=False, sitemap=False)
    def mollie_return(self, **data):
        if data:
            _logger.info("Received Mollie return data:\n%s", pprint.pformat(data))
            request.env['payment.transaction'].sudo()._handle_feedback_data('mollie', data)
        return request.redirect('/payment/status')

    @http.route(_notify_url, type='http', auth='public', methods=['GET', 'POST'], csrf=False, sitemap=False)
    def mollie_notify(self, **data):
        if data:
            _logger.info("Received Mollie notify data:\n%s", pprint.pformat(data))
            request.env['payment.transaction'].sudo()._handle_feedback_data('mollie', data)
            transaction = request.env["payment.transaction"].sudo().self.search([('reference', '=', data.get('ref')), ('provider', '=', 'mollie')])

            # Responding 200 as we do not want webhook call again
            if transaction.state in ['done', 'cancel', 'error']:
                return Response("OK", status=200)

        # Mollie will call webhook again if we respind other then 200
        return Response("Not Confirmed", status=418)
