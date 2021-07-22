# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import werkzeug
import logging

from odoo import http
from odoo.http import request, Response

_logger = logging.getLogger(__name__)


class MollieController(http.Controller):
    _return_url = "/payment/mollie/return"
    _notify_url = "/payment/mollie/notify"

    @http.route(_return_url, type='http', auth='public', methods=['GET', 'POST'], csrf=False, sitemap=False)
    def mollie_return(self, **data):
        if data:
            request.env['payment.transaction'].sudo()._handle_feedback_data('mollie', data)
        else:
            pass  # The customer has cancelled the payment, don't do anything
        return request.redirect('/payment/status')

    @http.route(_notify_url, type='http', auth='public', methods=['GET', 'POST'], csrf=False, sitemap=False)
    def mollie_notify(self, **data):
        if data:
            request.env['payment.transaction'].sudo()._handle_feedback_data('mollie', data)
        else:
            pass  # The customer has cancelled the payment, don't do anything
        return request.redirect('/payment/status')
