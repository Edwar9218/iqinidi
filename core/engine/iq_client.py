"""
core/engine/iq_client.py — IQ Option como fuente de datos del gráfico
============================================================================
Reemplaza a MetaTrader 5. Todo lo que el gráfico necesita de IQ Option vive
acá, detrás de funciones simples:

    listar_activos()            lista de activos (divisas, cripto, acciones…) con
                                estado abierto/cerrado y % de pago
    fetch_candles(...)          historial de velas -> DataFrame para el motor
    fetch_older(...)            velas más viejas ("Cargar más historial")
    vela_actual(...)            última vela EN VIVO (precio que se mueve)
    estado()                    estado de la conexión, para la barra de arriba

Decisiones (y por qué):

  * UNA sola conexión compartida y UN candado. La librería iqoptionapi no es
    segura entre hilos: sus pedidos comparten variables internas. Todo lo que
    pide algo al servidor entra por `_lock`. Lo único que NO lo usa es leer la
    vela en vivo (`vela_actual`), que solo mira un diccionario que el propio
    WebSocket va llenando — así el precio se mueve aunque otra carga esté en
    curso.

  * Los nombres de activos salen de `get_all_init_v2` (lo que IQ Option ofrece
    HOY: EURUSD-op, EURUSD-OTC, USDCOP-OTC…), no de la lista fija que trae la
    librería, que está desactualizada. Se vuelcan a `OP_code.ACTIVES`, que es
    la tabla nombre -> id que la librería consulta para pedir velas. Se evita
    `update_ACTIVES_OPCODE()` a propósito: pide forex/cfd/crypto/digital y en
    este entorno se queda colgada.

  * La librería tiene bucles `while ...: pass` que giran para siempre si el
    servidor no contesta. Cada llamada se hace en un hilo con tiempo máximo
    (`_con_timeout`); si vence, se descarta la conexión y se reconecta.

  * Hora: IQ Option entrega los tiempos en UTC. Se les suma un desfase fijo
    (UTC_OFFSET_HORAS, por defecto -5 = Colombia) para que el eje del gráfico
    muestre TU hora, igual que MT5 mostraba la hora de su servidor. El desfase
    se aplica en un solo lugar y se quita al pedir velas más viejas.

  * Solo cuenta PRACTICE y solo lectura: este módulo no opera ni manda órdenes.
"""

import logging
import os
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

import pandas as pd

logger = logging.getLogger(__name__)


class IqError(Exception):
    """Algo falló hablando con IQ Option (credenciales, conexión, activo, datos)."""


# ── Temporalidades ──────────────────────────────────────────────────────
# nombre -> segundos por vela. Son los tamaños que IQ Option acepta de verdad
# (la librería los lista en IQ_Option.size). No existen H3/H6/MN1, por eso el
# selector de temporalidad ya no es el de MT5.
TIMEFRAMES = OrderedDict([
    ("S15", 15), ("S30", 30),
    ("M1", 60), ("M2", 120), ("M5", 300), ("M10", 600), ("M15", 900), ("M30", 1800),
    ("H1", 3600), ("H2", 7200), ("H4", 14400), ("H8", 28800), ("H12", 43200),
    ("D1", 86400), ("W1", 604800),
])
TIMEFRAME_DEFECTO = "M5"

VELAS_POR_PEDIDO = 1000       # tope que IQ Option entrega por cada get-candles
VELAS_DEFECTO = 1500          # cuántas velas trae "Cargar" (IQ_VELAS en el entorno lo cambia)
MAX_STREAMS = 8               # suscripciones en vivo abiertas a la vez
VIGENCIA_ACTIVOS_S = 45       # cuánto se reutiliza la lista de activos (abierto/cerrado cambia)
TIEMPO_PEDIDO_S = 30          # tope de espera por cada pedido a IQ Option

# Códigos de moneda, para reconocer los pares de divisas por su nombre.
MONEDAS = {
    "USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD", "NOK", "SEK", "PLN",
    "TRY", "ZAR", "MXN", "BRL", "SGD", "HKD", "THB", "INR", "RUB", "CNH", "CNY",
    "COP", "CLP", "ARS", "BOB", "DOP", "AED", "BDT", "IDR", "MYR", "NGN", "PHP",
    "SAR", "VND", "TWD", "KRW",
}

_lock = threading.RLock()
_api = None
_estado = {"conectado": False, "mensaje": "Sin conectar todavía.", "saldo": None}

_activos = {"t": 0.0, "lista": [], "por_nombre": {}}
_streams = OrderedDict()            # (nombre, size) -> momento en que se abrió
_streams_iniciando = set()
_streams_lock = threading.Lock()


# ═══════════════════════════════════════════════════════════════════════
# Configuración
# ═══════════════════════════════════════════════════════════════════════

def _leer_credenciales():
    """Correo y contraseña: variables de entorno IQ_EMAIL / IQ_PASSWORD o, si no
    están, el archivo credenciales.py de la raíz del proyecto."""
    email = os.environ.get("IQ_EMAIL")
    password = os.environ.get("IQ_PASSWORD")
    if not (email and password):
        try:
            import credenciales
            email = email or getattr(credenciales, "EMAIL", None)
            password = password or getattr(credenciales, "PASSWORD", None)
        except ImportError:
            pass
    if not email or not password or email.startswith("tu_correo") or password == "tu_contrasena":
        raise IqError("Falta tu correo y contraseña de IQ Option: ábrelos en credenciales.py "
                      "(en la carpeta del proyecto), guárdalo y reinicia arrancar.bat.")
    return email, password


def offset_segundos():
    """Desfase (segundos) que se suma al UTC de IQ Option para mostrar tu hora."""
    valor = os.environ.get("IQ_UTC_OFFSET_HORAS")
    if valor is None:
        try:
            import credenciales
            valor = getattr(credenciales, "UTC_OFFSET_HORAS", -5)
        except ImportError:
            valor = -5
    try:
        return int(round(float(valor) * 3600))
    except (TypeError, ValueError):
        return -5 * 3600


def velas_por_defecto():
    try:
        return max(50, int(os.environ.get("IQ_VELAS", VELAS_DEFECTO)))
    except ValueError:
        return VELAS_DEFECTO


# ═══════════════════════════════════════════════════════════════════════
# Conexión
# ═══════════════════════════════════════════════════════════════════════

def _con_timeout(funcion, segundos, *args):
    """Corre funcion(*args) en un hilo y espera como máximo `segundos`. Las
    funciones de iqoptionapi pueden quedarse girando para siempre: así no
    cuelgan el servidor. Si vence, la conexión se descarta (se reconecta sola
    en la próxima llamada)."""
    global _api
    salida = {}

    def correr():
        try:
            salida["valor"] = funcion(*args)
        except Exception as e:                       # noqa: BLE001
            salida["error"] = e

    hilo = threading.Thread(target=correr, daemon=True)
    hilo.start()
    hilo.join(segundos)
    if hilo.is_alive():
        _api = None
        _estado.update(conectado=False, mensaje="IQ Option no respondió a tiempo; se reconectará.")
        raise IqError("IQ Option no respondió a tiempo. Vuelve a intentarlo en unos segundos.")
    if "error" in salida:
        raise salida["error"]
    return salida.get("valor")


def _conectar():
    try:
        from iqoptionapi.stable_api import IQ_Option
    except ImportError as e:
        raise IqError("Falta instalar la librería: pip install -r requirements.txt") from e

    email, password = _leer_credenciales()
    _estado.update(conectado=False, mensaje="Conectando a IQ Option…")
    api = IQ_Option(email, password)
    try:
        ok, razon = _con_timeout(api.connect, 60)
    except IqError:
        _estado.update(conectado=False, mensaje="IQ Option no respondió al conectar.")
        raise
    if not ok:
        detalle = str(razon)
        _estado.update(conectado=False, mensaje=f"No se pudo conectar: {detalle}")
        raise IqError(f"No se pudo conectar a IQ Option: {detalle}")

    api.change_balance("PRACTICE")           # solo lectura y solo cuenta de práctica
    try:
        saldo = api.get_balance()
    except Exception:                         # noqa: BLE001
        saldo = None
    _estado.update(conectado=True, mensaje="Conectado a IQ Option (cuenta PRACTICE).", saldo=saldo)
    logger.info("[iq] conectado, saldo de práctica: %s", saldo)
    return api


def _cliente():
    """La conexión viva (se abre o reabre sola si hace falta)."""
    global _api
    with _lock:
        if _api is None or not _api.check_connect():
            _api = _conectar()
        return _api


def estado():
    """Para la barra de estado: conexión actual (sin intentar conectar)."""
    return {
        "conectado": bool(_api is not None and _estado["conectado"] and _api.check_connect()),
        "mensaje": _estado["mensaje"],
        "saldo": _estado["saldo"],
        "streams": [f"{n} {s}s" for (n, s) in list(_streams.keys())],
    }


def precalentar():
    """Conecta y baja la lista de activos en segundo plano al arrancar, así el
    primer "Cargar" no espera la conexión."""
    def trabajo():
        try:
            listar_activos(forzar=True)
        except Exception as e:                    # noqa: BLE001
            logger.warning("[iq] precalentar falló: %s", e)
    threading.Thread(target=trabajo, daemon=True, name="iq-precalentar").start()


# ═══════════════════════════════════════════════════════════════════════
# Activos
# ═══════════════════════════════════════════════════════════════════════

def _es_par_divisas(base):
    return len(base) == 6 and base[:3] in MONEDAS and base[3:] in MONEDAS


def _describir(nombre):
    """Clasificación liviana por el nombre: sirve para los filtros del buscador."""
    es_otc = nombre.endswith("-OTC")
    base = nombre[:-4] if es_otc else (nombre[:-3] if nombre.endswith("-op") else nombre)
    return {
        "mercado": "OTC" if es_otc else "Real",
        "grupo": "divisa" if _es_par_divisas(base) else "otro",
    }


def _leer_activos_de_iq():
    api = _cliente()
    init = _con_timeout(api.get_all_init_v2, 45)
    if not init:
        raise IqError("IQ Option no entregó la lista de activos. Inténtalo de nuevo.")

    por_nombre = OrderedDict()
    id_a_nombre = {}
    for tipo in ("binary", "turbo"):
        for clave, d in ((init.get(tipo) or {}).get("actives") or {}).items():
            bruto = str(d.get("name", ""))
            nombre = bruto[bruto.find(".") + 1:] if "." in bruto else bruto
            try:
                aid = int(d.get("id", clave))
            except (TypeError, ValueError):
                continue
            if not nombre:
                continue
            # Un id = un nombre. La librería busca el nombre por el id al recibir
            # velas en vivo (el PRIMERO que encuentre), así que un alias lo confundiría.
            if aid in id_a_nombre and id_a_nombre[aid] != nombre:
                continue
            id_a_nombre[aid] = nombre

            a = por_nombre.get(nombre)
            if a is None:
                a = {"nombre": nombre, "id": aid, "abierto": False, "pago": None, "tipos": []}
                a.update(_describir(nombre))
                por_nombre[nombre] = a
            a["tipos"].append(tipo)
            if d.get("enabled") and not d.get("is_suspended"):
                a["abierto"] = True
            try:
                pago = round(100.0 - float(d["option"]["profit"]["commission"]))
                a["pago"] = max(a["pago"] or 0, pago)
            except (KeyError, TypeError, ValueError):
                pass

    if not por_nombre:
        raise IqError("IQ Option devolvió una lista de activos vacía.")

    # Tabla nombre -> id que usa la librería (se modifica EN SITIO: otros módulos
    # de la librería la leen como OP_code.ACTIVES).
    import iqoptionapi.constants as OP_code
    OP_code.ACTIVES.clear()
    for nombre, a in por_nombre.items():
        OP_code.ACTIVES[nombre] = a["id"]
    return por_nombre


def listar_activos(forzar=False):
    """Lista de activos [{nombre, id, abierto, pago, tipos, mercado, grupo}], ordenada
    por nombre. Se reutiliza 45 s."""
    with _lock:
        vigente = (time.time() - _activos["t"]) < VIGENCIA_ACTIVOS_S and _activos["lista"]
        if forzar or not vigente:
            por_nombre = _leer_activos_de_iq()
            _activos["por_nombre"] = por_nombre
            _activos["lista"] = sorted(por_nombre.values(), key=lambda a: a["nombre"].lower())
            _activos["t"] = time.time()
        return [dict(a) for a in _activos["lista"]]


def _buscar_en_cache(texto):
    """Nombre exacto según la lista de activos ya descargada (sin esperar a nadie)."""
    t = (texto or "").strip().lower()
    por_nombre = _activos["por_nombre"]
    if not t or not por_nombre:
        return None
    minusculas = {n.lower(): n for n in por_nombre}
    if t in minusculas:
        return minusculas[t]
    variantes = [minusculas[v] for v in (t + "-op", t + "-otc") if v in minusculas]
    if variantes:
        abiertas = [v for v in variantes if por_nombre[v]["abierto"]]
        return (abiertas or variantes)[0]
    return None


def resolver_nombre(texto):
    """Nombre EXACTO del activo para lo que se escribió (sin importar mayúsculas).
    Si se escribió solo "EURUSD", usa EURUSD-op o EURUSD-OTC, el que esté abierto."""
    t = (texto or "").strip()
    if not t:
        raise IqError("Elige una divisa en el buscador.")
    nombre = _buscar_en_cache(t)
    if nombre:
        return nombre
    listar_activos()                      # sin lista, o lista vieja: se baja de nuevo
    nombre = _buscar_en_cache(t)
    if nombre:
        return nombre
    raise IqError(f"IQ Option no ofrece '{t}'. Búscalo en la lista (escribe parte del nombre).")


def _tamano(timeframe):
    tf = (timeframe or "").strip().upper()
    if tf not in TIMEFRAMES:
        raise IqError(f"Temporalidad '{timeframe}' no disponible. Usa una de: {', '.join(TIMEFRAMES)}.")
    return TIMEFRAMES[tf]


# ═══════════════════════════════════════════════════════════════════════
# Velas históricas
# ═══════════════════════════════════════════════════════════════════════

def _ahora_utc():
    try:
        ts = _api.get_server_timestamp() if _api is not None else None
        if ts:
            return int(ts)
    except Exception:                             # noqa: BLE001
        pass
    return int(time.time())


def _historial(api, nombre, size, total, hasta_ts=None):
    """Hasta `total` velas (más viejas -> más nuevas) que terminan en `hasta_ts`
    (UTC; por defecto, ahora). IQ Option entrega 1000 como máximo por pedido,
    así que se pide en tandas hacia atrás. Devuelve dicts crudos de la librería."""
    velas = {}
    fin = int(hasta_ts or _ahora_utc())
    for _ in range(40):
        faltan = total - len(velas)
        if faltan <= 0:
            break
        lote = _con_timeout(api.get_candles, TIEMPO_PEDIDO_S, nombre, size, min(VELAS_POR_PEDIDO, faltan), fin)
        if not lote:
            break
        antes = len(velas)
        for c in lote:
            velas.setdefault(int(c["from"]), c)
        if len(velas) == antes:         # el lote no trajo nada nuevo: ya no hay más historia
            break
        fin = min(int(c["from"]) for c in lote)
    ordenadas = [velas[k] for k in sorted(velas)]
    return ordenadas[-total:]


def _decimales(valores):
    """Decimales que usa el precio (para el eje del gráfico), mirando los últimos."""
    mejor = 0
    for v in list(valores)[-300:]:
        s = f"{float(v):.8f}".rstrip("0")
        mejor = max(mejor, len(s.split(".")[1]) if "." in s else 0)
    return min(max(mejor, 2), 8)


def fetch_candles(symbol, timeframe, cantidad=None):
    """Velas para el motor de cálculo: DataFrame [datetime, open, high, low, close,
    volume] con la hora ya desplazada (ver offset_segundos) y `attrs["digits"]`.
    Devuelve (DataFrame, texto de origen)."""
    nombre = resolver_nombre(symbol)
    size = _tamano(timeframe)
    total = max(50, min(int(cantidad or velas_por_defecto()), velas_por_defecto()))

    with _lock:
        api = _cliente()
        crudas = _historial(api, nombre, size, total)
    if not crudas:
        raise IqError(f"IQ Option no devolvió velas para {nombre} {timeframe.upper()}.")

    _asegurar_stream(nombre, size)

    off = offset_segundos()
    df = pd.DataFrame({
        "datetime": pd.to_datetime([int(c["from"]) + off for c in crudas], unit="s"),
        "open": [float(c["open"]) for c in crudas],
        "high": [float(c["max"]) for c in crudas],
        "low": [float(c["min"]) for c in crudas],
        "close": [float(c["close"]) for c in crudas],
        "volume": [float(c.get("volume") or 0) for c in crudas],
    }).reset_index(drop=True)
    df.attrs["digits"] = _decimales(df["close"])
    df.attrs["nombre_exacto"] = nombre
    etiqueta = f"IQ Option — {nombre} {timeframe.upper()} ({len(df)} velas, hasta {df['datetime'].iloc[-1]})"
    return df, etiqueta


def fetch_older(symbol, timeframe, antes_de_ts, cantidad=500):
    """Velas MÁS VIEJAS que `antes_de_ts` (tiempo del gráfico, o sea ya desplazado)
    para "Cargar más historial". Lista de {time, open, high, low, close}."""
    nombre = resolver_nombre(symbol)
    size = _tamano(timeframe)
    off = offset_segundos()
    antes_utc = int(antes_de_ts) - off
    with _lock:
        api = _cliente()
        crudas = _historial(api, nombre, size, int(cantidad) + 1, hasta_ts=antes_utc)
    return [_a_vela(c, off) for c in crudas if int(c["from"]) < antes_utc][-int(cantidad):]


def _a_vela(c, off):
    return {"time": int(c["from"]) + off, "open": float(c["open"]), "high": float(c["max"]),
            "low": float(c["min"]), "close": float(c["close"])}


# ═══════════════════════════════════════════════════════════════════════
# Precio en vivo
# ═══════════════════════════════════════════════════════════════════════
# IQ Option empuja cada cambio de la vela en formación por el WebSocket
# ("candle-generated"); la librería lo va guardando en un diccionario. El
# navegador consulta ese diccionario cada ~0,5 s (vela_actual) y actualiza la
# última vela: el precio se mueve como en MT5, sin recargar el gráfico.

def _asegurar_stream(nombre, size):
    clave = (nombre, size)
    with _streams_lock:
        if clave in _streams:
            _streams.move_to_end(clave)
            return
        if clave in _streams_iniciando:
            return
        _streams_iniciando.add(clave)
    threading.Thread(target=_abrir_stream, args=(nombre, size), daemon=True, name="iq-stream").start()


def _abrir_stream(nombre, size):
    clave = (nombre, size)
    try:
        with _lock:
            api = _cliente()
            _con_timeout(api.start_candles_stream, 45, nombre, size, 10)
        with _streams_lock:
            _streams[clave] = time.time()
            sobrantes = []
            while len(_streams) > MAX_STREAMS:
                sobrantes.append(_streams.popitem(last=False)[0])
        for viejo in sobrantes:
            try:
                _con_timeout(api.stop_candles_stream, 30, viejo[0], viejo[1])
            except Exception:                     # noqa: BLE001
                pass
    except Exception as e:                        # noqa: BLE001
        logger.warning("[iq] no se pudo abrir el stream de %s %ss: %s", nombre, size, e)
    finally:
        with _streams_lock:
            _streams_iniciando.discard(clave)


def vela_actual(symbol, timeframe):
    """Última vela en formación y la anterior (ya cerrada, con sus valores
    finales) o None si todavía no llegó nada. No toma el candado: no espera a
    otras cargas."""
    nombre = _buscar_en_cache(symbol)     # solo la lista ya descargada: nunca espera el candado
    if nombre is None:
        return None
    size = _tamano(timeframe)
    _asegurar_stream(nombre, size)

    api = _api
    if api is None:
        return None
    if not api.check_connect():
        threading.Thread(target=lambda: _reconectar(), daemon=True).start()
        return None

    d = None
    for _ in range(3):                    # el WebSocket puede modificar el dict mientras se lee
        try:
            d = api.get_realtime_candles(nombre, size)
            if not d:
                return None
            claves = sorted(d.keys())
            break
        except RuntimeError:
            continue
        except Exception:                 # noqa: BLE001
            return None
    else:
        return None

    off = offset_segundos()
    vela = _a_vela(d[claves[-1]], off)
    if len(claves) > 1:
        vela["previa"] = _a_vela(d[claves[-2]], off)
    return vela


def _reconectar():
    try:
        _cliente()
    except Exception as e:                        # noqa: BLE001
        logger.warning("[iq] reconexión falló: %s", e)


# ═══════════════════════════════════════════════════════════════════════
# Hora
# ═══════════════════════════════════════════════════════════════════════

def hora_info():
    """Hora actual que se muestra (UTC + desfase) y el desfase en horas."""
    off = offset_segundos()
    ahora = datetime.now(timezone.utc) + timedelta(seconds=off)
    return {
        "offset_segundos": off,
        "offset_horas": off / 3600,
        "fuente": "configurado",
        "hora": ahora.strftime("%Y-%m-%dT%H:%M:%S"),
    }
