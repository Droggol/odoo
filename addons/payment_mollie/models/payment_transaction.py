# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    mollie_card_token = fields.Char()
    mollie_payment_method = fields.Char()
    mollie_payment_issuer = fields.Char()
