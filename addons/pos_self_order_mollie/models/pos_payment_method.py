# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from werkzeug import urls

from odoo import models, fields


class PosPaymentMethod(models.Model):
    _inherit = "pos.payment.method"

    def payment_request_from_kiosk(self, order):
        if self.use_payment_terminal != 'mollie':
            return super().payment_request_from_kiosk(order)
        else:
            payment_data = {
                'description': order.name,
                'pos_reference': order.pos_reference,
                'currency': order.currency_id.name,
                'amount': order.amount_total,
                'session_id': order.session_id.id,
                'config_id': order.session_id.config_id.id,
                'order_type': 'kiosk'
            }
            result = self.mollie_payment_request(payment_data)
            if result and result.get('status') == 'open':
                return True
            else:
                return False

    def _get_mollie_webhook_url(self, data):
        webhook_url = super()._get_mollie_webhook_url(data)
        if data.get('order_type') == 'kiosk':
            base_url = self.get_base_url()
            webhook_url = urls.url_join(base_url, f'/pos_mollie_kiosk/webhook/{self.id}')
        return webhook_url

    def _mollie_process_webhook_kiosk(self, webhook_data):
        self.ensure_one()
        payment_status = self._get_mollie_payment_status(webhook_data.get('id'))
        if payment_status and payment_status.get('status'):
            order_reference = payment_status['metadata'].get('pos_reference')
            order_sudo = self.env['pos.order'].sudo().search([('pos_reference', '=', order_reference)], limit=1)
            order = order_sudo.sudo(False).with_user(order_sudo.session_id.config_id.self_ordering_default_user_id).with_company(order_sudo.session_id.config_id.company_id)

            if payment_status['status'] == 'paid':
                payment_amount = float(payment_status['amount']['value'])
                order.add_payment({
                    'amount': payment_amount,
                    'payment_date': fields.Datetime.now(),
                    'payment_method_id': self.id,
                    'transaction_id': payment_status['id'],
                    'payment_status': payment_status.get('status'),
                    'pos_order_id': order.id
                })
                order.action_pos_order_paid()
                order._send_order()
            if order.config_id.self_ordering_mode == 'kiosk':
                order.env['bus.bus']._sendone(f'pos_config-{order.config_id.access_token}', 'PAYMENT_STATUS', {
                    'payment_result': payment_status['status'] == 'paid' and 'Success' or payment_status['status'],
                    'order': order._export_for_self_order(),
                })
