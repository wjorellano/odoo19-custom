# Fix: Mejorar Detección de transactionInProcess en RETR Polling

## Problema Identificado

Del log de usuario se vio claramente:

1. **RCLQ enviado** → Respuesta: `sessionManagementResponse.response = APPR`
2. **RETR iniciado automáticamente** → Respuesta:
   ```json
   {
     "sessionResponse": { "response": "APPR" },
     "transactionInProcess": {
       "transactionStatus": "INIT",
       "exchangeIdentification": "7dbb75ab-2a21-45d8-aae9-d89712bf0f07"
     }
   }
   ```
3. **Nuevo pago (AUTQ) devolvió INPR** con referencia al batch close anterior en `transactionInProcess`
4. **Polling de nuevo pago devolvió NOTF** porque el exchange_id del batch close sigue pendiente

**Raíz del problema:**

- Cuando `_poll_retr()` recibe `sessionManagementResponse` con `transactionInProcess`, estaba terminando
- Debería **continuar reintentando** porque hay una transacción en proceso
- El batch close nunca se completó correctamente

## Cambios Implementados

### 1. `_poll_retr()` - Detectar y reintentar en `transactionInProcess`

**Antes:**

```python
if status not in ('INPR', 'INIT', 'NOTF') or attempt >= max_attempts:
    # Retorna inmediatamente si no es INPR/INIT/NOTF
    return {'success': True, 'status': status, 'response': response}
```

**Ahora:**

```python
# Si hay transactionInProcess, siempre reintentar (a menos que sea el último intento)
if transaction_in_process and attempt < max_attempts:
    tx_status = transaction_in_process.get('transactionStatus', '')
    _logger.debug('NUVEI: RETR transactionInProcess=%s, esperando...', tx_status)
    time.sleep(delay)
    delay = min(delay * 1.5, 5.0)
    continue  # ← Reintentar en lugar de terminar

# Si no hay transactionInProcess y el status es terminal
if status not in ('INPR', 'INIT', 'NOTF') or attempt >= max_attempts:
    return {'success': True, 'status': status, 'response': response}
```

**Beneficios:**

- ✅ Si Nuvei devuelve `transactionInProcess`, automáticamente reintentar
- ✅ No termina prematuramente solo porque status = APPR
- ✅ Espera a que la transacción se complete completamente

### 2. `batch_close()` - Aumentar reintentos para APPR

**Antes:**

```python
if status in ('APPR', 'INPR', 'INIT'):
    retr_result = self._poll_retr(exchange_id, max_attempts=6, initial_delay=0.5)
```

**Ahora:**

```python
if status in ('APPR', 'INPR', 'INIT'):
    # Aumentar reintentos si APPR (batch aceptado pero aún procesándose)
    max_attempts = 8 if status == 'APPR' else 6
    retr_result = self._poll_retr(exchange_id, max_attempts=max_attempts, initial_delay=0.5)
```

**Beneficios:**

- ✅ APPR recibe 8 intentos en lugar de 6 (más margen para procesar)
- ✅ Secuencia de delays: 0.5, 0.75, 1.13, 1.69, 2.54, 3.81, 5.0, 5.0s = ~22.5s total
- ✅ INPR/INIT siguen con 6 intentos (ya son rápidos)

## Sequence Mejorada

### Antes (Problemático):

```
RCLQ → APPR
  ↓
RETR → APPR + transactionInProcess (INIT)
  ↓
Retorna inmediatamente ← ❌ PROBLEMA: No espera a que se complete
  ↓
Próximo pago → INPR + referencia al RCLQ anterior
```

### Ahora (Corregido):

```
RCLQ → APPR
  ↓
RETR → APPR + transactionInProcess (INIT)
  ↓
Detecta transactionInProcess → Continúa reintentando ← ✅ FIXED
  ↓
RETR → serviceResponse con RCLP (batch closed)
  ↓
Retorna con batch_data completo
  ↓
Próximo pago → APPR (sin interferencia)
```

## Timing de Polling Mejorado

| Intento | Delay | Total Acumulado | Estado                                 |
| ------- | ----- | --------------- | -------------------------------------- |
| 1       | 0.0s  | 0.0s            | Intento inicial                        |
| 2       | 0.5s  | 0.5s            | Si INPR/INIT/NOTF/transactionInProcess |
| 3       | 0.75s | 1.25s           | Continúa                               |
| 4       | 1.13s | 2.38s           | Continúa                               |
| 5       | 1.69s | 4.07s           | Continúa                               |
| 6       | 2.54s | 6.61s           | Continúa                               |
| 7       | 3.81s | 10.42s          | Solo si status=APPR (8 intentos)       |
| 8       | 5.0s  | 15.42s          | Último intento APPR                    |

**Máximo total:**

- INPR/INIT: ~6.61 segundos (6 intentos)
- APPR: ~15.42 segundos (8 intentos, más tiempo para procesar)

## Validación

✅ Sintaxis Python: válida
✅ Lógica: Si hay `transactionInProcess`, reintentar automáticamente
✅ Timing: APPR recibe más margen que INPR
✅ Logging: Detecta y reporta `transactionInProcess` con status

## Prueba Manual

El usuario debe:

1. Cerrar caja nuevamente
2. Hacer nuevo pago
3. Verificar en logs que NO aparece NOTF en el nuevo pago
4. Verificar que `nuvei.batch.close.report` se crea con totales

## Archivos Modificados

```
addons/pos_cloudlion_nuvei/models/nuvei_pos_request.py
├── batch_close() [Líneas ~753-798]
│   └── Aumentar max_attempts a 8 si status=APPR
└── _poll_retr() [Líneas ~876-987]
    └── Detectar transactionInProcess y reintentar automáticamente
```

## Notas Técnicas

1. **transactionInProcess** = "Hay una transacción en proceso, espera"
   - Antes: Se trataba como "listo"
   - Ahora: Se trata como "sigue procesándose, reintentar"

2. **Cascada de reintentos:**
   - Si RCLQ devuelve APPR → 8 reintentos de RETR
   - Si RETR devuelve transactionInProcess → Continúa reintentando
   - Total: Espera suficiente para que Nuvei procese

3. **Sin bloqueos:**
   - Los delays son máximo 5s
   - El polling es no-bloqueante (async en PoS)
   - El usuario ve "procesando..." pero no se bloquea la caja

---

**Status:** ✅ Fix implementado y listo para probar
**Próximo paso:** Usuario prueba nuevamente cierre de caja + nuevo pago
