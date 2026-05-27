/** @odoo-module **/
/**
 * Template XML para recibos de pago Nuvei en el POS.
 * Se muestra como parte del recibo de la orden.
 */
import { Component, xml } from "@odoo/owl";

export class NuveiPaymentReceipt extends Component {
    static template = xml`
        <div class="nuvei-receipt" t-if="props.data">
            <div style="text-align: center; margin: 8px 0; border-top: 1px dashed #000; padding-top: 8px;">
                <strong>--- CARD PAYMENT ---</strong>
            </div>
            <table style="width: 100%; font-size: 12px;">
                <tr t-if="props.data.card_type">
                    <td>Card:</td>
                    <td style="text-align: right;"><t t-esc="props.data.card_type"/></td>
                </tr>
                <tr t-if="props.data.card_number">
                    <td>Number:</td>
                    <td style="text-align: right;"><t t-esc="props.data.card_number"/></td>
                </tr>
                <tr t-if="props.data.entry_mode">
                    <td>Entry:</td>
                    <td style="text-align: right;"><t t-esc="entryModeLabel"/></td>
                </tr>
                <tr t-if="props.data.auth_code">
                    <td>Authorization:</td>
                    <td style="text-align: right;"><t t-esc="props.data.auth_code"/></td>
                </tr>
                <tr t-if="props.data.approval_text">
                    <td>Result:</td>
                    <td style="text-align: right;"><t t-esc="props.data.approval_text"/></td>
                </tr>
                <tr t-if="props.data.terminal_reference">
                    <td>Ref. Terminal:</td>
                    <td style="text-align: right;"><t t-esc="props.data.terminal_reference"/></td>
                </tr>
                <tr t-if="props.data.host_invoice">
                    <td>Invoice:</td>
                    <td style="text-align: right;"><t t-esc="props.data.host_invoice"/></td>
                </tr>
            </table>
            <div t-if="props.data.receipt_text" style="margin-top: 8px; border-top: 1px dashed #000; padding-top: 4px;">
                <pre style="font-size: 10px; white-space: pre-wrap;"><t t-esc="props.data.receipt_text"/></pre>
            </div>
            <div style="border-bottom: 1px dashed #000; margin: 8px 0;"/>
        </div>
    `;
    static props = {
        data: { type: Object, optional: true },
    };

    get entryModeLabel() {
        const modes = {
            'C': 'Chip (EMV)',
            'S': 'Magnetic Stripe',
            'T': 'Contactless (Tap)',
            'M': 'Manual',
            'F': 'Fallback',
        };
        return modes[this.props.data?.entry_mode] || this.props.data?.entry_mode || '';
    }
}
