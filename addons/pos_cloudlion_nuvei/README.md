# POS CloudLion x Nuvei

Integración de terminales de pago **Nuvei OMNI Channel ISO20022 v2.51** para Odoo 19 POS.

## Descripción

Conecta tu Punto de Venta de Odoo con terminales Nuvei usando el protocolo OMNI Channel ISO20022 via API RESTful HTTPS. Permite procesar pagos con tarjeta de crédito/débito directamente desde el POS.

## Funcionalidades

| Funcionalidad | Estado |
|---|---|
| Pago con tarjeta (CRDP/AUTQ) | ✅ Completo |
| Reembolso (RFND/RFNQ) | ✅ Completo |
| Pre-autorización (RQPA/FAUQ) | ✅ Completo |
| Completar pre-auth (CMPN/CMPV) | ✅ Completo |
| Anulación/Void (RVSL/FMPV) | ✅ Completo |
| Cancelación en proceso (FCXL) | ✅ Completo |
| Cierre de lote (RCLQ) | ✅ Completo |
| Test de conexión (NOTI) | ✅ Completo |
| Polling de estado (RETR/SASQ) | ✅ Completo |
| Historial de transacciones | ✅ Completo |
| Impresión de recibos | ✅ Completo |

## Requisitos

- Odoo 19 CE/EE
- Cuenta de comercio Nuvei con acceso OMNI Channel
- Terminal ID (TID) y Authentication Key de Nuvei
- Device ID (PID) configurado en Nuvei Cloud Service

## Instalación

1. Copiar el módulo en el directorio de addons de Odoo
2. Actualizar la lista de módulos: `Ajustes > Actualizar lista de módulos`
3. Instalar el módulo `POS CloudLion x Nuvei`

## Configuración

1. Ir a `Punto de Venta > Configuración > Métodos de Pago`
2. Crear un nuevo método de pago o editar uno existente
3. Seleccionar **Nuvei** como terminal de pago
4. Completar los campos:
   - **Device ID (PID)**: ID de la caja registradora
   - **Terminal ID (TID)**: ID del terminal físico
   - **Authentication Key**: Clave de autenticación
   - **Merchant ID**: ID del comercio
   - **API URL**: Se configura automáticamente según el modo (sandbox/producción)
   - **Modo Pruebas**: Activa/desactiva el sandbox
   - **Timeout**: Tiempo máximo de espera (default: 120s)
5. Presionar **Probar Conexión** para verificar las credenciales

## Flujo de Pago

```
  POS (Odoo)                    Nuvei Cloud                   Terminal
     │                              │                            │
     │── AUTQ (POST /services) ────>│                            │
     │<── INPR (en proceso) ────────│── Envía a terminal ───────>│
     │                              │                            │
     │── RETR (POST /session) ─────>│                            │
     │<── ACPT (procesando) ────────│                            │
     │                              │                            │
     │── RETR (polling) ───────────>│<── AUTP (resultado) ──────│
     │<── AUTP (datos del pago) ────│                            │
     │                              │                            │
     └── Actualiza línea de pago    │                            │
```

## Estructura del Módulo

```
pos_cloudlion_nuvei/
├── __init__.py
├── __manifest__.py
├── README.md
├── i18n/                          # Traducciones
│   ├── pos_cloudlion_nuvei.pot
│   └── es.po
├── models/
│   ├── __init__.py
│   ├── nuvei_pos_request.py       # Helper HTTPS (sin ORM)
│   ├── pos_payment_method.py      # Métodos ORM para el POS JS
│   └── pos_payment.py             # Campos extra en pos.payment
├── security/
│   └── ir.model.access.csv
├── static/src/
│   ├── app/
│   │   ├── components/
│   │   │   └── nuvei_payment_receipt.js
│   │   └── utils/payment/
│   │       └── payment_nuvei.js   # PaymentInterface del POS
│   └── overrides/models/
│       └── pos_payment.js         # Patch del modelo POS
├── tests/
│   ├── __init__.py
│   ├── test_nuvei_pos_request.py  # Tests unitarios backend
│   └── test_pos_payment_method.py # Tests del modelo ORM
└── views/
    ├── pos_payment_method_views.xml
    └── pos_payment_nuvei_views.xml # Historial de transacciones
```

## Tests

### Tests unitarios de Odoo
```bash
odoo -d test_db -i pos_cloudlion_nuvei --test-enable --stop-after-init
```

### Tests standalone contra sandbox
```bash
# Configurar credenciales
export NUVEI_PID="TuPosID"
export NUVEI_TID="TuTerminalID"
export NUVEI_KEY="tu-auth-key"
export NUVEI_MERCHANT="tuMerchantID"

# Ejecutar tests
python3 test_flujo_completo.py      # Flujo completo de pago
python3 test_nuvei_ok.py            # Pruebas individuales
python3 test_nuvei_rest.py          # Descubrimiento de API
```

## Licencia

LGPL-3
