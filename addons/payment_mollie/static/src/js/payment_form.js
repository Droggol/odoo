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
        start: function () {
            this.mollieComponentLoaded = false;
            // Show apple pay option only for apple devices
            if (window.ApplePaySession && window.ApplePaySession.canMakePayments()) {
                this.$('input[data-mollie-method="applepay"]').closest('.o_payment_option_card').removeClass('d-none');
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
            var $creditCardContainer = this.$(`#o_payment_mollie_method_inline_form_${paymentOptionId} #o_mollie_component`);
            debugger;
            if (!$creditCardContainer.length || this.mollieComponentLoaded) {
                return this._super(...arguments);
            }
            return ajax.loadJS("https://js.mollie.com/v1/mollie.js").then(() => this._setupMollieComponent());
        },

        /**
         * @private
         */
        _setupMollieComponent: function () {

            var mollieProfileId = this.$('#o_mollie_component').data('profile_id');
            var mollieTestMode = this.$('#o_mollie_component').data('mode') === 'test';

            var context;
            this.trigger_up('context_get', {
                callback: function (ctx) {
                    context = ctx;
                },
            });
            var lang = context.lang || 'en_US';
            this.mollieComponent = Mollie(mollieProfileId, { locale: lang, testmode: mollieTestMode });
            this._createMollieComponent('cardHolder', '#mollie-card-holder');
            this._createMollieComponent('cardNumber', '#mollie-card-number');
            this._createMollieComponent('expiryDate', '#mollie-expiry-date');
            this._createMollieComponent('verificationCode', '#mollie-verification-code');
            this.mollieComponentLoaded = true;
        },

        /**
        * @private
        */
        _createMollieComponent: function (type, componentId) {
            var component = this.mollieComponent.createComponent(type);
            component.mount(componentId);

            var $componentError = this.$(`${componentId}-error`);
            component.addEventListener('change', function (ev) {
                if (ev.error && ev.touched) {
                    $componentError.text(ev.error);
                } else {
                    $componentError.text('');
                }
            });
        },

        _prepareTransactionRouteParams: function (provider, paymentOptionId, flow) {
            const transactionRouteParams = this._super(...arguments);
            if (provider !== 'mollie') {
                return transactionRouteParams;
            }
            const $checkedRadios = this.$('input[name="o_payment_radio"]:checked');
            let mollieData = {
                mollie_method: $checkedRadios.data('mollie-method'),
                payment_option_id: $checkedRadios.data('mollie-acquirer-id'),
            };
            if ($checkedRadios.data('mollie-issuers')) {
                mollieData['mollie_issuer'] = this.$(`#o_payment_mollie_method_inline_form_${paymentOptionId} .o_mollie_issuer.active`).data('mollie-issuer');
            }
            return {
                ...transactionRouteParams,
                ...mollieData
            };
        },

        _processDirectPayment: function (provider, acquirerId, processingValues) {
            if (provider !== 'mollie') {
                return this._super(...arguments);
            }
            window.location = processingValues.redirect_url;
        },

        _processPayment: function (provider, paymentOptionId, flow) {
            if (provider !== 'mollie') {
                return this._super(...arguments);
            }
            let transactionParams = this._prepareTransactionRouteParams('mollie', paymentOptionId, 'direct');
            const creditCardChecked = this.$('input[data-mollie-method="creditcard"]:checked').length == 1;
            if (creditCardChecked) {
                return this._prepareMollieCardToken()
                    .then((cardToken) => {
                        transactionParams['mollie_token'] = cardToken;
                        this._submitMollieTransaction(provider, paymentOptionId, transactionParams)
                    });
            } else {
                return this._submitMollieTransaction(provider, paymentOptionId, transactionParams);
            }
        },

        _prepareMollieCardToken: function () {
            return this.mollieComponent.createToken().then(result => {
                if (result.error) {
                    this.displayNotification({
                        type: 'danger',
                        title: _t("Error"),
                        message: result.error.message,
                        sticky: false,
                    });
                    this.enableButton();
                }
                return result.token || false;
            });
        },

        _submitMollieTransaction: function (provider, paymentOptionId, transactionParams) {
            return this._rpc({
                route: this.txContext.transactionRoute,
                params: transactionParams
            }).then(processingValues => {
                return this._processDirectPayment(provider, paymentOptionId, processingValues);
            }).guardedCatch(error => {
                error.event.preventDefault();
                this._displayError(
                    _t("Server Error"),
                    _t("We are not able to process your payment."),
                    error.message.data.message
                );
            });
        }


    });

});
