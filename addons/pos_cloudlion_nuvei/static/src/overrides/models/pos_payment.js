/**
 * Override del modelo PosPayment para Nuvei.
 *
 * Agrega campos al uiState de la línea de pago para almacenar
 * el exchange_id de Nuvei durante la transacción.
 *
 * El uiState es estado temporal del frontend que NO se persiste
 * en la base de datos. Se usa durante el flujo de pago para:
 *   - Hacer polling con el exchange_id correcto
 *   - Cancelar la transacción correcta
 *
 * Patrón basado en: pos_razorpay/static/src/overrides/models/pos_payment.js
 */
import { PosPayment } from "@point_of_sale/app/models/pos_payment";
import { patch } from "@web/core/utils/patch";

patch(PosPayment.prototype, {
  /**
   * Extiende el setup para agregar nuvei_exchange_id al estado UI.
   * Este valor se genera al iniciar un pago y se usa durante el polling.
   */
  setup(vals) {
    super.setup(vals);
    this.uiState = {
      ...this.uiState,
      nuvei_exchange_id: this.nuvei_exchange_id || null,
    };
  },

  /**
   * Al crear una línea de reembolso, copia el exchange_id de la
   * transacción original para poder enviar la reversa (RVSL).
   */
  updateRefundPaymentLine(refundedPaymentLine) {
    super.updateRefundPaymentLine(refundedPaymentLine);
    this.uiState.nuvei_exchange_id = refundedPaymentLine?.nuvei_exchange_id;
  },
});
