odoo.define('pos_cloudlion_nuvei.payment', function (require) {
    'use strict';

    const PaymentInterface = require('point_of_sale.PaymentInterface');
    const { Gui } = require('point_of_sale.Class');
    const { _t } = require('web.core');

    class PaymentNuvei extends PaymentInterface {
        /**
         * @override
         */
        async send_payment_request(cid) {
            const paymentLine = this.pos.get_order().get_paymentline(cid);
            if (!paymentLine) {
                return false;
            }

            const paymentMethod = paymentLine.payment_method;
            const order = this.pos.get_order();
            const invoiceNumber = order.name; // Usar el nombre de la orden como número de factura
            const cashierId = this.pos.get_cashier().user_id;

            const data = {
                amount: paymentLine.amount,
                transaction_type: 'CRDP', // Card Payment (Venta con tarjeta)
                invoice_number: invoiceNumber,
                cashier_id: cashierId,
                exchange_id: this._generate_exchange_id(), // Generar un ID único para esta transacción
            };

            // Llamar al método ORM del backend para iniciar el pago
            const result = await this.rpc({
                model: 'pos.payment.method',
                method: 'nuvei_make_payment_request',
                args: [[paymentMethod.id], data],
            });

            if (result.success) {
                // Almacenar el exchange_id para el polling de estado
                paymentLine.set_payment_status('waiting');
                paymentLine.nuvei_exchange_id = result.exchange_id;
                paymentLine.nuvei_timeout = result.timeout; // Almacenar timeout para la lógica de polling
                // Iniciar el polling para obtener actualizaciones de estado del terminal
                this._start_polling(paymentLine);
                return true;
            } else {
                paymentLine.set_payment_status('retry');
                this._show_error_message(result.message);
                return false;
            }
        }

        /**
         * Genera un UUID v4 para el exchangeIdentification.
         * @returns {string} UUID
         */
        _generate_exchange_id() {
            return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
                var r = Math.random() * 16 | 0, v = c == 'x' ? r : (r & 0x3 | 0x8);
                return v.toString(16);
            });
        }

        /**
         * Muestra un mensaje de error al usuario.
         * @param {string} message - Mensaje de error a mostrar.
         */
        _show_error_message(message) {
            Gui.showPopup('ErrorPopup', {
                title: _t("Error de Pago Nuvei"),
                body: message,
            });
        }

        // Aquí irían otros métodos de la interfaz de pago, como _start_polling, _poll_for_status, etc.
        // que son esenciales para el flujo completo de la transacción y el manejo de la respuesta del terminal.
    }

    return PaymentNuvei;
});