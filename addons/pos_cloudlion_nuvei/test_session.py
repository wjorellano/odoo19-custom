#!/usr/bin/env python3
"""
Script de prueba de sesión Nuvei - Status Check (STCK)
=====================================================
Conecta via SSL raw socket a terminal-poi-sandbox.nuvei.com:18080
y envía un OCsessionManagementRequest con exchangeAction=STCK
para validar credenciales y estado del terminal virtual.

NO realiza pagos. Solo verifica:
  1. Conexión SSL al servidor Nuvei Cloud
  2. Credenciales (PID/TID/AuthKey) válidas
  3. Estado del terminal (IDLE/BUSY/UNKN)
  4. Último heartbeat del terminal

Protocolo: OMNI Channel ISO20022 v2.51
  - Sección 2.3.1: Raw sockets usan puerto 18080, REST usa 443
  - Sección 2.2.2: Session header binario de 16 bytes (requerido en raw sockets)
  - Sección 4.1: OCsessionManagementRequest con STCK/NOTI
  - El sandbox NO tiene puerto 443 abierto (solo 18080)

Formato del mensaje raw socket (Sección 2.2.2):
  [4 bytes messageLength][2 bytes headerVersion][2 bytes protocolVersion]
  [8 bytes messageIdentification][JSON payload bytes]

  messageLength = len(headerVersion + protocolVersion + messageId + JSON)
                = 12 + len(JSON)

Uso:
  python3 test_session.py
  python3 test_session.py --tid CloudLionTer1 --pid CloudLionReg1 --key TU_AUTH_KEY
  python3 test_session.py --mode noti --key TU_AUTH_KEY --no-header  # sin session header
"""
import socket
import ssl
import json
import uuid
import sys
import select
import struct
import argparse
import time
from datetime import datetime, timezone


# ─── Configuración por defecto ──────────────────────────────────────────────
NUVEI_HOST = "terminal-poi-sandbox.nuvei.com"
NUVEI_PORT = 18080
DEFAULT_TID = "CloudLionTer1"      # Terminal ID (TID)
DEFAULT_PID = "CloudLionReg1"      # Register/POS ID (PID)
DEFAULT_MERCHANT = "7800199838"    # Merchant ID
# Auth Key: se pide al usuario si no se pasa por argumento
DEFAULT_AUTH_KEY = ""

# Versión del protocolo JSON (Sección 8 - ejemplos usan "2.0")
PROTOCOL_VERSION = "2.0"

# Session Header (Sección 2.2.2)
# headerVersion: '0100' → bytes 0x01, 0x00
HEADER_VERSION = b'\x01\x00'
# protocolVersion: '0244' → bytes 0x02, 0x44 (versión 2.44 — la actual del spec)
HEADER_PROTOCOL = b'\x02\x44'

# Timeout de lectura en segundos
READ_TIMEOUT = 15


def banner():
    """Imprime banner del script."""
    print()
    print("╔" + "═" * 62 + "╗")
    print("║" + " NUVEI SESSION TEST - Status Check (STCK) ".center(62) + "║")
    print("║" + " Raw Socket SSL · Puerto 18080 ".center(62) + "║")
    print("╚" + "═" * 62 + "╝")
    print()


def conectar(host, port):
    """
    Establece conexión SSL con el servidor Nuvei Cloud.
    El sandbox usa certificado que no valida con CAs estándar,
    así que deshabilitamos verificación (solo para sandbox).
    """
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw_sock.settimeout(15)
    sock = context.wrap_socket(raw_sock, server_hostname=host)

    print(f"  Conectando a {host}:{port} (SSL)...")
    sock.connect((host, port))
    print(f"  ✓ Conexión SSL establecida")
    print(f"    Cipher: {sock.cipher()}")
    print(f"    TLS version: {sock.version()}")
    return sock


# ─── Session Header binario (Sección 2.2.2) ────────────────────────────────

def build_session_header(json_bytes, msg_id=1):
    """
    Construye el session header binario de 16 bytes.

    Sección 2.2.2:
      messageLength (4 bytes, big-endian): cuenta todos los bytes DESPUÉS de sí mismo
        = 2 (headerVersion) + 2 (protocolVersion) + 8 (messageId) + len(json)
        = 12 + len(json)
      headerVersion (2 bytes): '0100' → 0x01, 0x00
      protocolVersion (2 bytes): '0244' → 0x02, 0x44
      messageIdentification (8 bytes, big-endian): ID numérico del intercambio
    """
    payload_after_length = 12 + len(json_bytes)

    header = struct.pack('>I', payload_after_length)  # 4 bytes messageLength
    header += HEADER_VERSION                           # 2 bytes headerVersion
    header += HEADER_PROTOCOL                          # 2 bytes protocolVersion
    header += struct.pack('>Q', msg_id)                # 8 bytes messageIdentification

    return header  # 16 bytes total


def parse_session_header(data):
    """
    Parsea el session header de 16 bytes en la respuesta.

    :return: (message_length, header_version, protocol_version, msg_id, remaining_data)
    """
    if len(data) < 16:
        return None

    msg_len = struct.unpack('>I', data[0:4])[0]
    hdr_ver = data[4:6].hex()
    proto_ver = data[6:8].hex()
    msg_id = struct.unpack('>Q', data[8:16])[0]

    return {
        'messageLength': msg_len,
        'headerVersion': hdr_ver,
        'protocolVersion': proto_ver,
        'messageIdentification': msg_id,
        'payload': data[16:],
    }


# ─── Envío y recepción ─────────────────────────────────────────────────────

def enviar_con_header(sock, payload, descripcion="", msg_id=1):
    """
    Envía un payload JSON con session header binario de 16 bytes (Sección 2.2.2).
    Este es el formato requerido para raw sockets en puerto 18080.

    :return: (éxito: bool, datos: dict|str|None)
    """
    json_bytes = json.dumps(payload).encode('utf-8')
    header = build_session_header(json_bytes, msg_id)
    message = header + json_bytes

    if descripcion:
        print(f"\n[→] {descripcion}")
    print(f"  Header: {header.hex()} ({len(header)} bytes)")
    print(f"  JSON:   {len(json_bytes)} bytes")
    print(f"  Total:  {len(message)} bytes")

    try:
        sock.sendall(message)
        print(f"  ✓ Enviado")
    except Exception as e:
        print(f"  ✗ Error enviando: {e}")
        return False, None

    return recibir_respuesta(sock)


def enviar_sin_header(sock, payload, descripcion=""):
    """
    Envía un payload JSON sin session header, delimitado por newline.
    Método usado en pagar.py original. Puede o no funcionar según el servidor.

    :return: (éxito: bool, datos: dict|str|None)
    """
    json_str = json.dumps(payload)
    message = (json_str + '\n').encode('utf-8')

    if descripcion:
        print(f"\n[→] {descripcion}")
    print(f"  Enviando {len(json_str)} bytes (JSON + newline, SIN session header)...")

    try:
        sock.sendall(message)
        print(f"  ✓ Enviado")
    except Exception as e:
        print(f"  ✗ Error enviando: {e}")
        return False, None

    return recibir_respuesta(sock)


def recibir_respuesta(sock):
    """
    Recibe la respuesta del servidor.
    Intenta detectar si viene con session header binario o como JSON puro.

    :return: (éxito: bool, datos: dict|str|None)
    """
    print(f"  Esperando respuesta (máx {READ_TIMEOUT}s)...")
    response_data = b''
    sock.setblocking(False)

    try:
        readable, _, _ = select.select([sock], [], [], READ_TIMEOUT)
        if readable:
            # Leer todo lo disponible
            deadline = time.time() + 3  # esperamos hasta 3s adicionales por chunks
            while time.time() < deadline:
                try:
                    chunk = sock.recv(8192)
                    if not chunk:
                        break
                    response_data += chunk
                    # Reset deadline al recibir datos
                    deadline = time.time() + 1
                except (socket.error, BlockingIOError):
                    # Sin más datos inmediatos, esperar un poco
                    time.sleep(0.1)
                    try:
                        r, _, _ = select.select([sock], [], [], 1)
                        if not r:
                            break  # No hay más datos
                    except:
                        break
        else:
            print(f"  ✗ Timeout: sin respuesta en {READ_TIMEOUT}s")
            return False, None
    except Exception as e:
        print(f"  ✗ Error leyendo: {e}")
        return False, None
    finally:
        sock.setblocking(True)

    if not response_data:
        print(f"  ✗ Respuesta vacía")
        return False, None

    print(f"  ← Recibidos {len(response_data)} bytes")
    print(f"  ← Hex primeros 20 bytes: {response_data[:20].hex()}")

    # Intentar parsear: ¿tiene session header binario?
    json_data = None

    # Método 1: Session header (16 bytes) + JSON
    if len(response_data) > 16:
        parsed = parse_session_header(response_data)
        if parsed:
            print(f"  ← Session Header detectado:")
            print(f"      messageLength: {parsed['messageLength']}")
            print(f"      headerVersion: {parsed['headerVersion']}")
            print(f"      protocolVersion: {parsed['protocolVersion']}")
            print(f"      messageId: {parsed['messageIdentification']}")

            payload_bytes = parsed['payload']
            try:
                json_data = json.loads(payload_bytes.decode('utf-8'))
                print(f"  ✓ JSON parseado desde session header")
                return True, json_data
            except (json.JSONDecodeError, UnicodeDecodeError):
                print(f"  ⚠ No se pudo parsear JSON después del header")
                print(f"      Payload raw: {payload_bytes[:200]}")

    # Método 2: JSON puro (sin header)
    response_str = response_data.decode('utf-8', errors='ignore').strip()
    for line in reversed(response_str.split('\n')):
        line = line.strip()
        if line:
            try:
                json_data = json.loads(line)
                print(f"  ✓ JSON parseado directamente (sin session header)")
                return True, json_data
            except json.JSONDecodeError:
                continue

    # Método 3: Mostrar raw si nada funcionó
    print(f"  ✗ No se pudo parsear la respuesta")
    print(f"    Raw (text): {response_str[:500]}")
    print(f"    Raw (hex):  {response_data[:100].hex()}")
    return False, response_str


# ─── Construcción de payloads ───────────────────────────────────────────────

def build_stck_request(tid, pid, auth_key):
    """
    Construye un OCsessionManagementRequest con STCK (Status Check).

    Sección 4.1 del spec:
      - messageFunction: SASQ
      - initiatingParty: POS (type=PID) con authenticationKey
      - POSComponent.exchangeAction: STCK

    STCK valida credenciales y retorna:
      - Estado del terminal
      - lastHeartbeat
      - Código de respuesta (APPR, INTP, RCPP, UNMP, VERS)
    """
    exchange_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

    return {
        'OCsessionManagementRequest': {
            'header': {
                'messageFunction': 'SASQ',
                'protocolVersion': PROTOCOL_VERSION,
                'exchangeIdentification': exchange_id,
                'creationDateTime': timestamp,
                'initiatingParty': {
                    'identification': pid,
                    'type': 'PID',
                    'authenticationKey': auth_key,
                },
                'recipientParty': {
                    'identification': tid,
                    'type': 'TID',
                },
            },
            'sessionManagementRequest': {
                'POSComponent': {
                    'POSGroupIdentification': {
                        'exchangeAction': 'STCK',
                        'exchangeIdentification': '',
                    },
                },
            },
        },
    }


def build_noti_request(tid, auth_key):
    """
    Construye un OCsessionManagementRequest con NOTI (Notificación/Heartbeat).

    Alternativa al STCK. Usado en pagar.py original.
    El initiatingParty se identifica como TID (perspectiva del terminal).

    Sección 4.1:
      - state: IDLE/BUSY (requerido solo para NOTI)
    """
    exchange_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

    return {
        'OCsessionManagementRequest': {
            'header': {
                'messageFunction': 'SASQ',
                'protocolVersion': PROTOCOL_VERSION,
                'exchangeIdentification': exchange_id,
                'creationDateTime': timestamp,
                'initiatingParty': {
                    'identification': tid,
                    'type': 'TID',
                    'authenticationKey': auth_key,
                },
            },
            'sessionManagementRequest': {
                'POIComponent': {
                    'POIGroupIdentification': {
                        'exchangeAction': 'NOTI',
                    },
                    'state': 'IDLE',
                },
            },
        },
    }


# ─── Análisis de respuestas ─────────────────────────────────────────────────

def analizar_respuesta(data):
    """
    Analiza la respuesta OCsessionManagementResponse.

    Sección 4.2:
      - sessionResponse.response: código de resultado
      - POIGroupIdentification.lastHeartbeat: timestamp
      - POIGroupIdentification.exchangeAction: estado
    """
    print("\n" + "─" * 60)
    print("  ANÁLISIS DE RESPUESTA")
    print("─" * 60)

    if not isinstance(data, dict):
        print(f"  Tipo inesperado: {type(data)}")
        return

    # Buscar la respuesta en distintas claves posibles
    mgmt = data.get('OCsessionManagementResponse', {})
    if not mgmt:
        print(f"  No contiene OCsessionManagementResponse")
        print(f"  Claves: {list(data.keys())}")
        return

    # Header de respuesta
    header = mgmt.get('header', {})
    print(f"\n  Header:")
    print(f"    messageFunction: {header.get('messageFunction', '?')}")
    print(f"    protocolVersion: {header.get('protocolVersion', '?')}")
    print(f"    exchangeId:      {header.get('exchangeIdentification', '?')}")
    print(f"    dateTime:        {header.get('creationDateTime', '?')}")

    init_party = header.get('initiatingParty', {})
    print(f"    initiatingParty: {init_party.get('identification', '?')} ({init_party.get('type', '?')})")

    # Session Management Response
    session = mgmt.get('sessionManagementResponse', {})

    # Código de respuesta
    session_resp = session.get('sessionResponse', {})
    resp_code = session_resp.get('response', '')
    resp_reason = session_resp.get('responseReason', '')
    resp_info = session_resp.get('additionalResponseInformation', '')

    print(f"\n  Respuesta de sesión:")
    print(f"    response:       {resp_code}")
    if resp_reason:
        print(f"    responseReason: {resp_reason}")
    if resp_info:
        print(f"    additionalInfo: {resp_info}")

    # Interpretar código de respuesta
    RESP_CODES = {
        'APPR': '✓ APROBADO — Credenciales válidas, terminal encontrado',
        'INTP': '✗ ERROR — Device ID (PID) o Authentication Key INVÁLIDOS',
        'RCPP': '✗ ERROR — Terminal ID (TID) no encontrado',
        'UNMP': '✗ ERROR — POS no asociado al terminal',
        'VERS': '✗ ERROR — Versión de protocolo no soportada',
        'SASP': '✓ Session Accepted — Sesión aceptada por el servidor',
    }
    interpretacion = RESP_CODES.get(resp_code, f'? Código desconocido: {resp_code}')
    print(f"    → {interpretacion}")

    # POIComponent (info del terminal)
    poi_comp = session.get('POIComponent', {})
    if isinstance(poi_comp, list):
        poi_comp = poi_comp[0] if poi_comp else {}

    poi_group = poi_comp.get('POIGroupIdentification', {})
    if isinstance(poi_group, list):
        poi_group = poi_group[0] if poi_group else {}

    if poi_group:
        print(f"\n  Terminal (POI):")
        for k, v in poi_group.items():
            print(f"    {k}: {v}")

    poi_id = poi_comp.get('POIIdentification', {})
    if poi_id:
        print(f"\n  Terminal Identification:")
        for k, v in poi_id.items():
            print(f"    {k}: {v}")

    # POSComponent
    pos_comp = session.get('POSComponent', {})
    if isinstance(pos_comp, list):
        pos_comp = pos_comp[0] if pos_comp else {}
    pos_group = pos_comp.get('POSGroupIdentification', {})
    if isinstance(pos_group, list):
        pos_group = pos_group[0] if pos_group else {}

    if pos_group:
        print(f"\n  POS:")
        for k, v in pos_group.items():
            print(f"    {k}: {v}")

    # transactionInProcess (si hay una transacción en curso)
    tx_in_process = session.get('transactionInProcess', {})
    if tx_in_process:
        print(f"\n  Transacción en proceso:")
        print(f"    status:     {tx_in_process.get('transactionStatus', '?')}")
        print(f"    exchangeId: {tx_in_process.get('exchangeIdentification', '?')}")

    # Estado general
    state = session.get('state', poi_comp.get('state', ''))
    if state:
        print(f"\n  Estado del terminal: {state}")


# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Prueba de sesión Nuvei - Status Check (STCK)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python3 test_session.py --key f78c3a2c-7d6d-4e07-89de-c9446ad4e4b7
  python3 test_session.py --tid CloudLionTer1 --pid CloudLionReg1 --key MI_KEY
  python3 test_session.py --mode noti --key MI_KEY_DEL_TERMINAL
  python3 test_session.py --no-header --key MI_KEY  # sin session header binario
        """
    )
    parser.add_argument('--host', default=NUVEI_HOST, help=f'Host (default: {NUVEI_HOST})')
    parser.add_argument('--port', type=int, default=NUVEI_PORT, help=f'Puerto (default: {NUVEI_PORT})')
    parser.add_argument('--tid', default=DEFAULT_TID, help=f'Terminal ID (default: {DEFAULT_TID})')
    parser.add_argument('--pid', default=DEFAULT_PID, help=f'POS/Register ID (default: {DEFAULT_PID})')
    parser.add_argument('--key', default=DEFAULT_AUTH_KEY, help='Authentication Key')
    parser.add_argument('--merchant', default=DEFAULT_MERCHANT, help=f'Merchant ID (default: {DEFAULT_MERCHANT})')
    parser.add_argument('--mode', choices=['stck', 'noti', 'both'], default='both',
                        help='Tipo de prueba: stck, noti, both (default: both)')
    parser.add_argument('--no-header', action='store_true', dest='no_header',
                        help='Enviar sin session header binario (como pagar.py)')
    parser.add_argument('--raw', action='store_true', help='Mostrar JSON crudo completo')

    args = parser.parse_args()

    banner()

    # Pedir auth key si no se pasó
    auth_key = args.key
    if not auth_key:
        auth_key = input("  Auth Key (del correo de Nuvei): ").strip()
        if not auth_key:
            print("\n  ✗ Authentication Key es requerida")
            sys.exit(1)
        print()

    use_header = not args.no_header
    header_label = "CON session header (16 bytes)" if use_header else "SIN session header (JSON + newline)"

    # Mostrar configuración
    print("  Configuración:")
    print(f"    Host:        {args.host}:{args.port}")
    print(f"    Terminal ID: {args.tid}")
    print(f"    POS ID:      {args.pid}")
    print(f"    Merchant:    {args.merchant}")
    print(f"    Auth Key:    {auth_key[:8]}...{auth_key[-4:]}")
    print(f"    Modo:        {args.mode}")
    print(f"    Transporte:  {header_label}")

    sock = None
    msg_counter = 1
    results = []

    try:
        # ─── PRUEBA NOTI ────────────────────────────────────────────────
        if args.mode in ('noti', 'both'):
            print(f"\n{'=' * 62}")
            print(f"  PRUEBA: NOTI (Heartbeat - perspectiva terminal)")
            print(f"{'=' * 62}")

            sock = conectar(args.host, args.port)
            payload = build_noti_request(args.tid, auth_key)

            if use_header:
                ok, data = enviar_con_header(sock, payload, "SASQ + NOTI", msg_counter)
            else:
                ok, data = enviar_sin_header(sock, payload, "SASQ + NOTI")
            msg_counter += 1

            if ok and data:
                if args.raw:
                    print(f"\n  JSON completo:")
                    print(json.dumps(data, indent=2))
                analizar_respuesta(data)
                results.append(('NOTI', True))
            else:
                results.append(('NOTI', False))

            try:
                sock.close()
            except:
                pass
            sock = None
            time.sleep(1)

        # ─── PRUEBA STCK ────────────────────────────────────────────────
        if args.mode in ('stck', 'both'):
            print(f"\n{'=' * 62}")
            print(f"  PRUEBA: STCK (Status Check - perspectiva POS)")
            print(f"{'=' * 62}")

            sock = conectar(args.host, args.port)
            payload = build_stck_request(args.tid, args.pid, auth_key)

            if use_header:
                ok, data = enviar_con_header(sock, payload, "SASQ + STCK", msg_counter)
            else:
                ok, data = enviar_sin_header(sock, payload, "SASQ + STCK")
            msg_counter += 1

            if ok and data:
                if args.raw:
                    print(f"\n  JSON completo:")
                    print(json.dumps(data, indent=2))
                analizar_respuesta(data)
                results.append(('STCK', True))
            else:
                results.append(('STCK', False))

            try:
                sock.close()
            except:
                pass
            sock = None

        # ─── RESUMEN ────────────────────────────────────────────────────
        print(f"\n{'=' * 62}")
        print("  RESUMEN")
        print(f"{'=' * 62}")
        for test_name, success in results:
            status = "✓ OK" if success else "✗ FALLÓ"
            print(f"    {test_name}: {status}")
        print(f"\n  Servidor:    {args.host}:{args.port}")
        print(f"  Transporte:  {header_label}")
        print()

        all_failed = all(not s for _, s in results) if results else True
        if all_failed and use_header:
            print("  SUGERENCIA: Si falló con session header, prueba sin él:")
            print(f"    python3 test_session.py --no-header --key {auth_key[:8]}...")
        elif all_failed and not use_header:
            print("  SUGERENCIA: Si falló sin header, prueba con el session header:")
            print(f"    python3 test_session.py --key {auth_key[:8]}...")
        print()

    except socket.timeout:
        print(f"\n  ✗ Timeout conectando a {args.host}:{args.port}")
        sys.exit(1)
    except ConnectionRefusedError:
        print(f"\n  ✗ Conexión rechazada por {args.host}:{args.port}")
        sys.exit(1)
    except Exception as e:
        print(f"\n  ✗ Error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        if sock:
            try:
                sock.close()
            except:
                pass


if __name__ == '__main__':
    main()
