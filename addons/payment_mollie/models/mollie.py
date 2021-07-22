# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo.osv import expression
from odoo import fields, models


class MolliePaymentMethod(models.Model):
    _name = 'mollie.payment.method'
    _description = 'Mollie payment method'
    _order = "sequence, id"

    name = fields.Char(translate=True)
    sequence = fields.Integer()
    acquirer_id = fields.Many2one('payment.acquirer', string='Acquirer')  # This will be always mollie
    method_code = fields.Char(string="Method code")
    payment_icon_ids = fields.Many2many('payment.icon', string='Supported Payment Icons')
    active = fields.Boolean(default=True)
    active_on_shop = fields.Boolean(string="Enabled on shop", default=True)
    country_ids = fields.Many2many('res.country', string='Country Availability')
    mollie_voucher_ids = fields.One2many('mollie.voucher.line', 'method_id', string='Mollie Voucher Config')

    # Hidden fields that are used for filtering methods
    supports_order_api = fields.Boolean(string="Supports Order API")
    supports_payment_api = fields.Boolean(string="Supports Payment API")
    payment_issuer_ids = fields.Many2many('mollie.payment.method.issuer', string='Issuers')


class MolliePaymentIssuers(models.Model):
    _name = 'mollie.payment.method.issuer'
    _description = 'Mollie payment method issuers'
    _order = "sequence, id"

    name = fields.Char()
    sequence = fields.Integer()
    acquirer_id = fields.Many2one('mollie.payment.method', string='Acquirer')
    payment_icon_ids = fields.Many2many('payment.icon', string='Supported Payment Icons')
    issuers_code = fields.Char()
    active = fields.Boolean(default=True)


class MollieVoucherLines(models.Model):
    _name = 'mollie.voucher.line'
    _description = 'Mollie voucher method'

    method_id = fields.Many2one('mollie.payment.method', string='Mollie Method')
    category_ids = fields.Many2many('product.category', string='Product Categories')
    product_ids = fields.Many2many('product.template', string='Products')
    mollie_voucher_category = fields.Selection([('meal', 'Meal'), ('eco', 'Eco'), ('gift', 'Gift')], required=True)


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    def _get_mollie_voucher_category(self):
        domain = [('product_ids', 'in', self.ids)]
        categories = self.mapped('categ_id')
        if categories:
            domain = expression.OR([domain, [('category_ids', 'parent_of', categories.ids)]])
        voucher_line = self.env['mollie.voucher.line'].search(domain, limit=1)
        return voucher_line and voucher_line.mollie_voucher_category or False
