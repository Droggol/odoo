# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import json
from odoo.addons.pos_mollie.controllers.main import PosMollieController
from odoo import http, fields
from odoo.http import request


class PosMollieKioskController(http.Controller):

    @http.route('/pos_mollie_kiosk/webhook/<int:payment_method_id>', type='http', methods=['POST'], auth='public', csrf=False)
    def webhook_kiosk(self, payment_method_id, **post):
        if not post.get('id'):
            return
        payment_method_sudo = request.env['pos.payment.method'].sudo().browse(payment_method_id)
        if payment_method_sudo.exists() and payment_method_sudo.use_payment_terminal == 'mollie':
            payment_method_sudo._mollie_process_webhook_kiosk(post)
        return "OK"    # send response to mark it as successful
