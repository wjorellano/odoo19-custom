/**
 * Integración de terminal de pago Nuvei para Odoo 19 POS.
 *
 * Implementa PaymentInterface para comunicarse con el terminal Nuvei
 * a través de llamadas ORM al backend (pos.payment.method).
 *
 * Flujo de pago (Retail Mode - sección 2.3.3 de OMNI Channel ISO20022):
 *   1. JS llama nuvei_make_payment_request → Backend envía AUTQ via POST /services
 *   2. Backend recibe respuesta con response=INPR (transacción en proceso)
 *   3. JS hace polling con nuvei_fetch_payment_status → Backend envía RETR via POST /session
 *   4. Mientras terminal procesa → Backend recibe NOTF o transactionInProcess
 *   5. Cuando terminal completa → Backend recibe serviceResponse (AUTP) con datos de tarjeta
 *   6. JS actualiza la línea de pago con transaction_data extraídos del AUTP
 *
 * Respuestas del backend (check_transaction_status):
 *   { completed: true, status: "APPR", transaction_data: {...} }  → Pago completado
 *   { completed: false, status: "ACPT" }  → Transacción en proceso, seguir polling
 *   { completed: false, status: "NOTF" }  → Aún no hay respuesta, seguir polling
 *   { completed: true, cancelled: true }   → Cancelado desde el terminal
 *
 * Patrón basado en: pos_razorpay/static/src/app/utils/payment/payment_razorpay.js
 */
import { _t } from "@web/core/l10n/translation";
import { PaymentInterface } from "@point_of_sale/app/utils/payment/payment_interface";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { register_payment_method } from "@point_of_sale/app/services/pos_store";

// Intervalo de polling: empieza en 2s y crece con backoff hasta max 8s
const POLLING_INTERVAL_MIN = 2000;
const POLLING_INTERVAL_MAX = 8000;
const POLLING_BACKOFF_FACTOR = 1.5;

// Tiempo máximo de inactividad por defecto (ms) - se sincroniza con backend
const DEFAULT_INACTIVITY_TIMEOUT = 20000;

export class PaymentNuvei extends PaymentInterface {
  // -------------------------------------------------------------------------
  // SETUP - Inicialización del estado interno
  // -------------------------------------------------------------------------

  setup() {
    super.setup(...arguments);
    // Timer para el polling de estado
    this.pollingTimeout = null;
    // Timer para cancelar por inactividad
    this.inactivityTimeout = null;
    // Bandera: se excedió el timeout de inactividad
    this.paymentTimedOut = false;
    // Timeout de inactividad en ms (sincronizado con backend)
    this.inactivityTimeoutMs = DEFAULT_INACTIVITY_TIMEOUT;
    // Intervalo actual de polling (crece con backoff exponencial)
    this.currentPollingInterval = POLLING_INTERVAL_MIN;
  }

  // -------------------------------------------------------------------------
  // INTERFAZ PÚBLICA (override de PaymentInterface)
  // -------------------------------------------------------------------------

  /**
   * Inicia el proceso de pago en el terminal Nuvei.
   * Llamado automáticamente al seleccionar el método de pago (fastPayments = true).
   *
   * @param {string} cid - UUID de la línea de pago
   * @returns {Promise<boolean>} false si se debe reintentar
   */
  sendPaymentRequest(cid) {
    super.sendPaymentRequest(cid);
    return this._processNuveiPayment(cid);
  }

  /**
   * Cancela una transacción de pago en proceso.
   * Llamado cuando el usuario elimina la línea de pago o presiona cancelar.
   *
   * @param {object} order - La orden actual
   * @param {string} cid - UUID de la línea de pago
   * @returns {Promise<boolean>}
   */
  sendPaymentCancel(order, cid) {
    super.sendPaymentCancel(order, cid);
    return this._cancelNuveiPayment();
  }

  /**
   * Reversa un pago ya completado (anulación/void).
   * Llamado desde la interfaz cuando se solicita reversa.
   *
   * @param {string} cid - UUID de la línea de pago
   * @returns {Promise<boolean>} true si la reversa fue exitosa
   */
  sendPaymentReversal(cid) {
    super.sendPaymentReversal(cid);
    return this._processNuveiReversal(cid);
  }

  // -------------------------------------------------------------------------
  // MÉTODOS INTERNOS - Llamadas ORM al backend
  // -------------------------------------------------------------------------

  /**
   * Ejecuta una llamada ORM silenciosa al modelo pos.payment.method.
   * Usa orm.silent para no mostrar spinners/bloqueos en la UI.
   *
   * @param {object} data - Datos a enviar al método del backend
   * @param {string} action - Nombre del método en pos.payment.method
   * @returns {Promise<object>} Respuesta del backend
   */
  _callNuvei(data, action) {
    console.log(`[NUVEI] → ORM call: ${action}`, JSON.stringify(data));
    return this.env.services.orm.silent
      .call("pos.payment.method", action, [[this.payment_method_id.id], data])
      .then((result) => {
        console.log(
          `[NUVEI] ← ORM response: ${action}`,
          JSON.stringify(result),
        );
        return result;
      })
      .catch(this._handleConnectionFailure.bind(this));
  }

  /**
   * Maneja errores de conexión con el servidor Odoo.
   * Pone la línea de pago en estado "retry" para que el usuario pueda reintentar.
   */
  _handleConnectionFailure(data = {}) {
    const line = this._pendingNuveiLine();
    if (line) {
      line.setPaymentStatus("retry");
    }
    this._showError(
      _t(
        "Could not connect to the Odoo server. Check your internet connection.",
      ),
    );
    return Promise.reject(data);
  }

  // -------------------------------------------------------------------------
  // PROCESO DE PAGO
  // -------------------------------------------------------------------------

  /**
   * Prepara los datos y envía la solicitud de pago al backend.
   * El backend crea el OCserviceRequest (AUTQ) y lo envía a Nuvei Cloud.
   *
   * @param {string} cid - UUID de la línea de pago
   * @returns {Promise<boolean>}
   */
  async _processNuveiPayment(cid) {
    const order = this.pos.getOrder();
    const line = order.getSelectedPaymentline();

    // Si es una orden de refund, usar función específica
    if (order.isRefund) {
      return this._processNuveiRefund(cid);
    }

    // Validar monto positivo para pagos normales
    if (line.amount <= 0) {
      this._showError(
        _t("Transactions with a negative amount cannot be processed."),
      );
      return false;
    }

    // Generar exchange_id único (UUID completo) para esta transacción
    // Nuvei espera un UUID válido en exchangeIdentification.
    const exchangeId = crypto.randomUUID();

    // Guardar exchange_id en el estado UI y en la línea para persistencia
    line.uiState.nuvei_exchange_id = exchangeId;
    line.update({ nuvei_exchange_id: exchangeId });

    // Preparar datos para el backend
    const data = {
      amount: Math.abs(line.amount),
      transaction_type: "CRDP",
      invoice_number: order.pos_reference || "",
      cashier_id: this.pos.user?.login || "",
      exchange_id: exchangeId,
    };
    console.log(
      `[NUVEI] ╔══ INICIANDO PAGO (Card Present) ════════════════════════════════\n` +
        `[NUVEI] ║ exchange_id: ${exchangeId}\n` +
        `[NUVEI] ║ monto:       $${data.amount}\n` +
        `[NUVEI] ║ tipo:        ${data.transaction_type}\n` +
        `[NUVEI] ║ factura:     ${data.invoice_number}\n` +
        `[NUVEI] ║ cajero:      ${data.cashier_id}\n` +
        `[NUVEI] ╚═════════════════════════════════════════════════════════`,
    );
    // Enviar solicitud al backend
    const response = await this._callNuvei(data, "nuvei_make_payment_request");
    return this._handlePaymentResponse(response);
  }
  /**
   * Procesa la respuesta inicial del backend después de enviar el pago.
   * Si fue exitosa (ACK recibido), inicia el polling para esperar la
   * respuesta del terminal (AUTP).
   *
   * @param {object} response - Respuesta del backend
   * @returns {Promise<boolean>}
   */
  _handlePaymentResponse(response) {
    const line = this._pendingNuveiLine();
    if (!line) {
      console.warn(
        "[NUVEI] _handlePaymentResponse: no hay línea de pago pendiente",
      );
      return Promise.resolve(false);
    }

    if (response?.error) {
      console.error(
        "[NUVEI] Error en respuesta inicial:",
        response.message || response.error,
      );
      line.setPaymentStatus("error");
      this._showError(response.message || response.error);
      this._cleanupTimers();
      return Promise.resolve(false);
    }

    // ✓ CRITICAL FIX: Nuvei retorna un ID diferente en transactionInProcess
    // que debe usarse para todas las solicitudes RETR (polling).
    // El backend extrae este ID y lo retorna como 'exchange_id' en la respuesta.
    // Debemos actualizar el exchange_id en la línea para usar el correcto en el polling.
    if (response?.exchange_id) {
      const originalId =
        line.uiState.nuvei_exchange_id || line.nuvei_exchange_id;
      line.uiState.nuvei_exchange_id = response.exchange_id;
      line.update({ nuvei_exchange_id: response.exchange_id });
      console.log(
        `[NUVEI] ✓ Actualizado exchange_id para polling:\n` +
          `[NUVEI]   Original: ${originalId}\n` +
          `[NUVEI]   Polling:  ${response.exchange_id}`,
      );
    }

    // El ACK fue recibido, ahora esperamos la respuesta del terminal
    console.log(
      "[NUVEI] ACK recibido del servidor — iniciando polling RETR...",
    );
    // Sincronizar timeout de inactividad con el backend
    if (response?.timeout) {
      this.inactivityTimeoutMs = response.timeout * 1000;
      console.log(
        `[NUVEI] Timeout sincronizado con backend: ${response.timeout}s`,
      );
    }
    // Reiniciar intervalo de polling para nueva transacción
    this.currentPollingInterval = POLLING_INTERVAL_MIN;
    line.setPaymentStatus("waitingCard");
    return this._waitForPaymentConfirmation();
  }

  // -------------------------------------------------------------------------
  // POLLING - Espera por resultado del terminal
  // -------------------------------------------------------------------------

  /**
   * Polling recursivo que consulta el estado de la transacción cada N segundos.
   * Envía SASQ + RETR al backend, que lo reenvía a Nuvei Cloud.
   *
   * Continúa hasta que:
   *   - Se recibe OCserviceResponse (AUTP) → transacción completada
   *   - Se recibe error del terminal → transacción fallida
   *   - Se agota el timeout global → se cancela automáticamente
   *   - El usuario navega fuera de la pantalla de pago
   *
   * IMPORTANTE: El timeout es GLOBAL desde el inicio del pago (no se reinicia con cada polling).
   * Esto asegura que después de N segundos, la transacción se cancele sin importar
   * si hay respuestas del servidor o no.
   *
   * @returns {Promise<object|false>} Respuesta final o false
   */
  _waitForPaymentConfirmation() {
    const line = this._pendingNuveiLine();
    if (!line || line.payment_status === "retry") {
      return Promise.resolve(false);
    }

    const exchangeId =
      line.uiState.nuvei_exchange_id || line.nuvei_exchange_id || "";
    if (!exchangeId) {
      this._showError(
        _t("Internal error: missing exchange_id for the transaction."),
      );
      return Promise.resolve(false);
    }

    // Iniciar timer de inactividad GLOBAL (NO se reinicia con cada polling)
    // Después de inactivityTimeoutMs, se cancelará automáticamente
    this.paymentTimedOut = false;
    this._startInactivityTimer();

    const pollStatus = async (resolve, reject) => {
      clearTimeout(this.pollingTimeout);

      // Si el usuario salió de la pantalla de pago, dejar de hacer polling
      if (this.pos.router.state.current !== "PaymentScreen") {
        return resolve(false);
      }

      // Si se agotó el tiempo de inactividad, terminar polling
      if (this.paymentTimedOut) {
        // La cancelación ya fue enviada por _startInactivityTimer()
        // Solo mostrar mensaje y terminar el polling
        line.setPaymentStatus("retry");
        this._showError(
          _t("Transaction was cancelled automatically. You can try again."),
        );
        this._cleanupTimers();
        return resolve(false);
      }

      // Consultar estado al backend
      const data = { exchange_id: exchangeId };
      const response = await this._callNuvei(
        data,
        "nuvei_fetch_payment_status",
      );

      if (response?.error) {
        //Error en polling — puede ser timeout o conexión
        const isTimeoutError =
          response.message &&
          (response.message.includes("Timeout") ||
            response.message.includes("connection") ||
            response.message.includes("connection"));

        if (isTimeoutError) {
          // Timeout de conexión — reintentar automáticamente
          console.warn(
            "[NUVEI] TIMEOUT/CONEXIÓN detectado. Reintentando con nuvei_retry_fetch_payment_status...",
          );

          // Esperar 3 segundos y reintentar
          await new Promise((r) => setTimeout(r, 3000));

          const retryResponse = await this._callNuvei(
            data,
            "nuvei_retry_fetch_payment_status",
          ).catch(() => {
            return {
              error: true,
              message: "Retry failed after timeout",
            };
          });

          // Si el reintenyo también falló, mostrar error y cancelar
          if (retryResponse?.error) {
            console.error(
              "[NUVEI] Retry failed after timeout:",
              retryResponse.message,
            );
            line.setPaymentStatus("retry");
            this._showError(
              _t(
                "Connection timeout - The transaction may be on the terminal. Try again in 60 seconds or verify it manually.",
              ),
            );
            this._cleanupTimers();
            return resolve(false);
          }

          // El reintenyo fue exitoso — continuar con la respuesta
          response = retryResponse;
          console.log("[NUVEI] ✓ Reintenyo exitoso después de timeout");
        } else {
          // Error no relacionado con timeout
          console.error(
            "[NUVEI] Error en polling:",
            response.message || response.error,
          );
          line.setPaymentStatus("error");
          this._showError(response.message || response.error);
          this._cleanupTimers();
          return resolve(false);
        }
      }

      // Evaluar estado de la transacción
      console.log(
        `[NUVEI] Polling RETR — completed=${response?.completed} ` +
          `status=${response?.status} cancelled=${response?.cancelled}`,
      );

      // El backend devuelve: { completed: bool, status: str, cancelled: bool, transaction_data: dict }
      if (response?.cancelled) {
        // Transacción cancelada desde el terminal (TXCN)
        // Cancelar INMEDIATAMENTE sin esperar timeout
        console.warn(
          `[NUVEI] Transacción CANCELADA desde el terminal (TXCN) — status=${response?.status}`,
        );
        line.setPaymentStatus("error");
        this._showError(_t("The transaction was cancelled from the terminal."));
        this._cleanupTimers();
        return resolve(false);
      } else if (response?.completed) {
        // Transacción completada — verificar si fue aprobada o rechazada
        const txStatus = response.status || "";

        // ✓ Usar el flag 'success' del backend para validar si fue realmente aprobado
        // Nuvei envía transaction_data incluso para pagos DECL (Declined),
        // asi que NO podemos confiar en su existencia. Usamos 'success' del backend.
        if (response?.success === true) {
          // ✓ Pago APROBADO (APPR o PART)
          console.log(
            `[NUVEI] ╔══ PAGO APROBADO ══════════════════════════════════════════\n` +
              `[NUVEI] ║ status: ${txStatus}\n` +
              `[NUVEI] ║ transaction_data: ${JSON.stringify(response.transaction_data)}\n` +
              `[NUVEI] ╚═════════════════════════════════════════════════════════`,
          );
          this._updatePaymentLine(line, response);
          line.setPaymentStatus("done");
          this._cleanupTimers();
          return resolve(response);
        } else {
          // ✗ Pago RECHAZADO (DECL, etc.)
          console.warn(`[NUVEI] ✗ Transacción RECHAZADA: ${txStatus}`);
          line.setPaymentStatus("error");
          this._showError(
            response.message || _t("Transaction declined: %s", txStatus),
          );
          this._cleanupTimers();
          return resolve(false);
        }
      } else {
        // Transacción en proceso (ACPT, NOTF, etc.) — seguir polling
        const interval =
          Math.round((this.currentPollingInterval / 1000) * 10) / 10;
        console.log(
          `[NUVEI] En proceso (${response?.status || "?"}) — próximo poll en ${interval}s`,
        );
        // NO reiniciar el timer de inactividad aqui — el timeout es GLOBAL desde el inicio
        this.pollingTimeout = setTimeout(
          pollStatus,
          this.currentPollingInterval,
          resolve,
          reject,
        );
        // Incrementar intervalo con backoff exponencial (hasta el máximo)
        this.currentPollingInterval = Math.min(
          this.currentPollingInterval * POLLING_BACKOFF_FACTOR,
          POLLING_INTERVAL_MAX,
        );
      }
    };

    return new Promise(pollStatus);
  }

  /**
   * Inicia el timer de inactividad GLOBAL.
   * Si pasan N segundos (sin reseteo), cancela automáticamente.
   * Se ejecuta UNA SOLA VEZ al comenzar el polling.
   */
  _startInactivityTimer() {
    clearTimeout(this.inactivityTimeout);

    const timeoutSeconds = this.inactivityTimeoutMs / 1000;
    console.log(
      `[NUVEI] Timer de inactividad iniciado: ${timeoutSeconds}s (GLOBAL, no se reinicia con polling)`,
    );

    this.inactivityTimeout = setTimeout(async () => {
      this.paymentTimedOut = true;

      const line = this._pendingNuveiLine();
      if (line) {
        const exchangeId =
          line.uiState.nuvei_exchange_id || line.nuvei_exchange_id || "";

        console.warn(
          `[NUVEI] TIMEOUT DESPUÉS DE ${timeoutSeconds}s — Cancelando automáticamente (exchange_id: ${exchangeId})`,
        );

        // Enviar cancelación automática al backend (con flag auto_cancel=true)
        const cancelResult = await this._cancelNuveiPayment(true);

        console.log(
          `[NUVEI] Cancelación automática enviada — resultado: ${cancelResult ? "✓ OK" : "✗ FALLÓ"}`,
        );

        // Mostrar mensaje de cancelación inmediatamente en el UI
        line.setPaymentStatus("retry");
        this._showError(
          _t("Transaction was cancelled automatically. You can try again."),
        );
        this._cleanupTimers();
      }
    }, this.inactivityTimeoutMs);
  }

  /**
   * Cancela una transacción en proceso.
   * Envía CUCL + FCXL al backend para forzar la cancelación.
   *
   * @param {boolean} isAutoCancel - true si es cancelación automática por timeout
   * @returns {Promise<boolean>}
   */
  async _cancelNuveiPayment(isAutoCancel = false) {
    const line = this._pendingNuveiLine();
    if (!line) {
      return true;
    }

    const cancelReason = isAutoCancel ? "AUTOMATIC TIMEOUT" : "Manual";
    console.log(
      `[NUVEI] Cancelación ${cancelReason} — exchange_id=${
        line.uiState.nuvei_exchange_id || line.nuvei_exchange_id
      }`,
    );
    const data = {
      exchange_id: line.uiState.nuvei_exchange_id || line.nuvei_exchange_id,
      auto_cancel: isAutoCancel,
    };
    const response = await this._callNuvei(data, "nuvei_cancel_payment");

    this._cleanupTimers();

    if (response?.error) {
      this._showError(response.message || response.error);
      return false;
    }
    return true;
  }

  /**
   * Procesa un refund (devolución) completo con polling.
   * Se usa cuando order.isRefund = true.
   * Inicia el refund y hace polling hasta que se complete.
   *
   * @param {string} cid - UUID de la línea de pago
   * @returns {Promise<boolean>}
   */
  async _processNuveiRefund(cid) {
    const order = this.pos.getOrder();
    const line = order.getSelectedPaymentline();

    // Generar exchange_id único (UUID completo) para esta transacción de refund
    // Nuvei espera un UUID válido en exchangeIdentification.
    const exchangeId = crypto.randomUUID();
    line.uiState.nuvei_exchange_id = exchangeId;

    // Preparar datos para el backend
    // CRITICAL: Incluir original_transaction_id para que el terminal sepa
    // a qué transacción aplicar el refund. Esto viene de line.transaction_id
    // cuando es una orden de refund.
    const data = {
      amount: Math.abs(line.amount),
      transaction_type: "RFND", // Tipo de refund
      invoice_number: order.pos_reference || "",
      cashier_id: this.pos.user?.login || "",
      exchange_id: exchangeId,
      original_transaction_id: line.transaction_id || "", // ← CRITICAL: TX ID de la transacción original
    };

    console.log(
      `[NUVEI] ╔══ INICIANDO REFUND ═══════════════════════════════════════\n` +
        `[NUVEI] ║ exchange_id:           ${exchangeId}\n` +
        `[NUVEI] ║ monto:                 $${data.amount}\n` +
        `[NUVEI] ║ tipo:                  RFND (Refund)\n` +
        `[NUVEI] ║ factura:               ${data.invoice_number}\n` +
        `[NUVEI] ║ cajero:                ${data.cashier_id}\n` +
        `[NUVEI] ║ original_tx_id:        ${data.original_transaction_id}\n` +
        `[NUVEI] ╚═════════════════════════════════════════════════════════`,
    );

    line.setPaymentStatus("waitingCard");

    // Enviar solicitud de refund al backend usando el endpoint correcto
    // Usa nuvei_make_refund_request para que sea consistente con voids
    const response = await this._callNuvei(data, "nuvei_make_refund_request");

    if (response?.error) {
      line.setPaymentStatus("retry");
      this._showError(response.message || response.error);
      return false;
    }

    if (!response?.success) {
      line.setPaymentStatus("error");
      this._showError(response?.message || _t("Error processing refund"));
      return false;
    }

    // ✓ CRITICAL FIX: Usar el exchange_id retornado por el backend para polling
    // (puede ser diferente del ID original si Nuvei retorna transactionInProcess)
    if (response?.exchange_id) {
      const originalId = line.uiState.nuvei_exchange_id;
      line.uiState.nuvei_exchange_id = response.exchange_id;
      console.log(
        `[NUVEI] Refund: actualizado exchange_id para polling: ${originalId} → ${response.exchange_id}`,
      );
    }

    // Iniciar polling para esperar respuesta del terminal
    return this._waitForRefundConfirmation();
  }

  /**
   * Polling para esperar la confirmación de un refund.
   * Similar a _waitForPaymentConfirmation pero para refunds.
   *
   * @returns {Promise<object|false>}
   */
  _waitForRefundConfirmation() {
    const line = this._pendingNuveiLine();
    if (!line || line.payment_status === "retry") {
      return Promise.resolve(false);
    }

    const exchangeId = line.uiState.nuvei_exchange_id;
    if (!exchangeId) {
      this._showError(
        _t("Internal error: missing exchange_id for the refund."),
      );
      return Promise.resolve(false);
    }

    this.paymentTimedOut = false;
    this._startInactivityTimer();

    const pollStatus = async (resolve, reject) => {
      clearTimeout(this.pollingTimeout);

      if (this.pos.router.state.current !== "PaymentScreen") {
        return resolve(false);
      }

      if (this.paymentTimedOut) {
        line.setPaymentStatus("retry");
        this._showError(
          _t("Refund was cancelled automatically. You can try again."),
        );
        this._cleanupTimers();
        return resolve(false);
      }

      const data = { exchange_id: exchangeId };
      const response = await this._callNuvei(
        data,
        "nuvei_fetch_payment_status",
      );

      if (response?.error) {
        const isTimeoutError =
          response.message &&
          (response.message.includes("Timeout") ||
            response.message.includes("connection") ||
            response.message.includes("connection"));

        if (isTimeoutError) {
          console.warn(
            "[NUVEI] TIMEOUT/CONEXIÓN detectado en refund. Reintentando...",
          );
          await new Promise((r) => setTimeout(r, 3000));

          const retryResponse = await this._callNuvei(
            data,
            "nuvei_retry_fetch_payment_status",
          ).catch(() => {
            return {
              error: true,
              message: "Retry failed after timeout",
            };
          });

          if (retryResponse?.error) {
            console.error(
              "[NUVEI] Refund retry failed:",
              retryResponse.message,
            );
            line.setPaymentStatus("retry");
            this._showError(
              _t("Refund timeout - The transaction may be on the terminal."),
            );
            this._cleanupTimers();
            return resolve(false);
          }

          response = retryResponse;
          console.log("[NUVEI] ✓ Reintenyo exitoso en refund");
        } else {
          console.error(
            "[NUVEI] Error en refund:",
            response.message || response.error,
          );
          line.setPaymentStatus("error");
          this._showError(response.message || response.error);
          this._cleanupTimers();
          return resolve(false);
        }
      }

      console.log(
        `[NUVEI] Polling REFUND — completed=${response?.completed} ` +
          `status=${response?.status} cancelled=${response?.cancelled}`,
      );

      if (response?.cancelled) {
        // Refund cancelado desde el terminal
        console.warn(
          `[NUVEI] Refund CANCELADO desde el terminal — status=${response?.status}`,
        );
        line.setPaymentStatus("error");
        this._showError(_t("The refund was cancelled from the terminal."));
        this._cleanupTimers();
        return resolve(false);
      } else if (response?.completed) {
        const txStatus = response.status || "";

        if (response?.success === true) {
          // ✓ Refund APROBADO
          console.log(
            `[NUVEI] ╔══ REFUND APROBADO ════════════════════════════════════════\n` +
              `[NUVEI] ║ status: ${txStatus}\n` +
              `[NUVEI] ║ transaction_data: ${JSON.stringify(response.transaction_data)}\n` +
              `[NUVEI] ╚═══════════════════════════════════════════════════════════`,
          );
          this._updatePaymentLine(line, response);
          line.setPaymentStatus("done");
          this._cleanupTimers();
          return resolve(response);
        } else {
          // ✗ Refund RECHAZADO
          console.warn(`[NUVEI] ✗ Refund RECHAZADO: ${txStatus}`);
          line.setPaymentStatus("error");
          this._showError(
            response.message || _t("Refund declined: %s", txStatus),
          );
          this._cleanupTimers();
          return resolve(false);
        }
      } else {
        // Refund en proceso
        const interval =
          Math.round((this.currentPollingInterval / 1000) * 10) / 10;
        console.log(
          `[NUVEI] Refund en proceso (${response?.status || "?"}) — próximo poll en ${interval}s`,
        );
        this.pollingTimeout = setTimeout(
          pollStatus,
          this.currentPollingInterval,
          resolve,
          reject,
        );
        this.currentPollingInterval = Math.min(
          this.currentPollingInterval * POLLING_BACKOFF_FACTOR,
          POLLING_INTERVAL_MAX,
        );
      }
    };

    return new Promise(pollStatus);
  }

  /**
   * Envía una reversa/anulación de una transacción ya completada.
   * Usa RVSL (reversal) en el backend.
   *
   * @param {string} cid - UUID de la línea de pago
   * @returns {Promise<boolean>}
   */
  async _processNuveiReversal(cid) {
    const order = this.pos.getOrder();
    const line = order.getSelectedPaymentline();

    if (!line?.transaction_id) {
      this._showError(
        _t("Cannot reverse: missing original transaction reference."),
      );
      return false;
    }

    line.setPaymentStatus("waitingCard");

    const data = {
      original_transaction_id: line.transaction_id,
      transaction_type: "CRDP",
      invoice_number: order.pos_reference || "",
      cashier_id: this.pos.user?.login || "",
    };

    const response = await this._callNuvei(data, "nuvei_make_refund_request");

    if (response?.error) {
      line.setPaymentStatus("retry");
      this._showError(response.message || response.error);
      return false;
    }

    if (response?.success) {
      this._updatePaymentLine(line, response);
      line.setPaymentStatus("done");
      return true;
    }

    return false;
  }

  // -------------------------------------------------------------------------
  // UTILIDADES
  // -------------------------------------------------------------------------

  /**
   * Busca la línea de pago pendiente de tipo Nuvei en la orden actual.
   * @returns {object|undefined} La línea de pago o undefined
   */
  _pendingNuveiLine() {
    return this.pos.getPendingPaymentLine("nuvei");
  }

  /**
   * Actualiza los campos de la línea de pago con los datos del terminal.
   * Los campos provienen de la respuesta AUTP del terminal Nuvei.
   *
   * @param {object} line - Línea de pago a actualizar
   * @param {object} response - Respuesta procesada del backend
   */
  _updatePaymentLine(line, response) {
    const txData = response.transaction_data || {};
    console.log(
      "[NUVEI] Actualizando línea de pago con:",
      JSON.stringify(txData),
    );

    // Construir texto del recibo del terminal (customer copy)
    let receiptText = "";
    if (txData.receipts && txData.receipts.length > 0) {
      const customerReceipt =
        txData.receipts.find((r) => r.type === "CRCPT") || txData.receipts[0];
      receiptText = customerReceipt?.content || "";
    }

    line.update({
      transaction_id: txData.transaction_id || "",
      card_type: txData.card_type || "",
      card_no: txData.card_number || "",
      cardholder_name: txData.cardholder_name || "",
      payment_method_authcode: txData.auth_code || "",
      payment_ref_no: txData.sale_reference || txData.terminal_reference || "",
      card_brand: txData.account_type || "",
      payment_method_payment_mode: txData.entry_mode || "",
      nuvei_exchange_id:
        txData.exchange_id || line.uiState.nuvei_exchange_id || "",
      nuvei_reference_no: txData.terminal_reference || "",
      nuvei_card_type: txData.card_type || "",
      nuvei_card_number: txData.card_number || "",
      nuvei_auth_code: txData.auth_code || "",
      nuvei_entry_mode: txData.entry_mode || "",
      nuvei_approval_text: txData.approval_text || "",
      nuvei_receipt_text: receiptText,
    });
  }

  /**
   * Limpia todos los timers activos (polling + inactividad).
   * Debe llamarse al completar, cancelar o fallar una transacción.
   */
  _cleanupTimers() {
    clearTimeout(this.pollingTimeout);
    clearTimeout(this.inactivityTimeout);
    this.pollingTimeout = null;
    this.inactivityTimeout = null;
    this.paymentTimedOut = false;
  }

  /**
   * Muestra un diálogo de error al usuario.
   *
   * @param {string} errorMsg - Mensaje de error
   * @param {string} title - Título del diálogo (opcional)
   */
  _showError(errorMsg, title) {
    this.env.services.dialog.add(AlertDialog, {
      title: title || _t("Error Nuvei"),
      body: errorMsg,
    });
  }

  /**
   * Limpia los timers al cerrar la pantalla de pago.
   * Override de PaymentInterface.close().
   */
  close() {
    this._cleanupTimers();
  }
}

// Registrar el método de pago "nuvei" con la clase PaymentNuvei.
// Esto conecta la selección use_payment_terminal='nuvei' con esta clase.
register_payment_method("nuvei", PaymentNuvei);
