odoo.define('mollie.payment.form', function (require) {
    "use strict";

    const checkoutForm = require('payment.checkout_form');
    const ajax = require('web.ajax');


    checkoutForm.include({
        events: _.extend({
            'click .o_mollie_issuer': '_clickIssuer',
        }, checkoutForm.prototype.events),

        /**
         * @override
         */
        init: function () {
            this.mollie_loaded = false;
            this.mollieJSURL = "https://js.mollie.com/v1/mollie.js";
            return this._super.apply(this, arguments);
        },

        /**
         * @override
         */
        willStart: function () {
            var self = this;
            self.libPromise = ajax.loadJS(self.mollieJSURL);
            return this._super.apply(this, arguments).then(function () {
                return self.libPromise;
            });
        },

        /**
         * @override
         */
        start: function () {
            if (window.ApplePaySession && window.ApplePaySession.canMakePayments()) {
                this.$('input[data-methodname="applepay"]').closest('.o_payment_acquirer_select').removeClass('d-none');
            }
            return this._super.apply(this, arguments);
        },

        /**
         * @private
         * @param {MouseEvent} ev
         */
        _clickIssuer: function (ev) {
            var $container = $(ev.currentTarget).closest('.o_mollie_issuer_container');
            $container.find('.o_mollie_issuer').removeClass('active');
            $(ev.currentTarget).addClass('active');
        },

        _prepareInlineForm: function (provider, paymentOptionId, flow) {
            if (provider !== 'mollie') {
                return this._super(...arguments);
            }

            this._setPaymentFlow('direct');
            
            console.log(provider, paymentOptionId, flow);
        }

    });

});
