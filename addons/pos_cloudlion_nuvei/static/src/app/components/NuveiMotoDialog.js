/**
 * NuveiMotoDialog — diálogo modal para elegir el modo de entrada de tarjeta.
 *
 * Aparece antes de enviar una transacción cuando nuvei_allow_moto está activo.
 * Devuelve un Promise que resuelve con:
 *   { moto: false }  → Card Present (flujo normal)
 *   { moto: true }   → MOTO/Manual Entry (MOTOIndicator=true en el request)
 *   null             → usuario cerró el diálogo sin elegir (cancelar)
 */
import { Component } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";

export class NuveiMotoDialog extends Component {
    static template = "pos_cloudlion_nuvei.NuveiMotoDialog";
    static components = { Dialog };
    static props = {
        title: { type: String, optional: true },
        close: Function,   // injected by the dialog service
        confirm: Function, // callback: (moto: boolean) => void
    };

    get title() {
        return this.props.title || _t("Select Entry Mode");
    }

    /**
     * Called when the user clicks one of the entry-mode buttons.
     * @param {boolean} moto - true = MOTO, false = Card Present
     */
    confirm(moto) {
        this.props.confirm(moto);
        this.props.close();
    }
}
