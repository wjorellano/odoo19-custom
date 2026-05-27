/** @odoo-module **/
/**
 * Patch para PaymentLine - Oculta el botón "Force done" para todos los pagos.
 *
 * El botón "Force done" no debe estar disponible para pagos electrónicos.
 * Se oculta via CSS.
 */

// Inyectar CSS para ocultar el botón "Force done"
const style = document.createElement("style");
style.textContent = `
    .payment-summary .button.send_force_done {
        display: none !important;
    }
    .paymentline .button.send_force_done {
        display: none !important;
    }
`;
document.head.appendChild(style);
