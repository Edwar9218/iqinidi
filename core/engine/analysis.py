"""
core/engine/analysis.py — cálculo del gráfico (framework-agnostic)
============================================================================
Versión reducida para el proyecto "iq-grafico": solo queda lo que usa la
pestaña Gráfico. Las velas ya NO vienen de MetaTrader 5 sino de IQ Option
(ver iq_client.py); todo el cálculo de canales, Kalman y Smart Money Flow es
EXACTAMENTE el mismo del proyecto original: auto_channels.py,
smart_money_flow.py y channel_breakout_status.py no se tocaron.
"""

import logging
import math
import os
import sys

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from . import auto_channels as ac        # noqa: E402  (lógica de canales + Kalman, SIN CAMBIOS)
try:
    from . import smart_money_flow as smf   # noqa: E402
except ImportError:
    smf = None

try:
    from . import channel_breakout_status as cbs   # noqa: E402
except ImportError:
    cbs = None

from . import iq_client                  # noqa: E402  (fuente de velas: IQ Option)
from . import trading_defaults           # noqa: E402  (defaults de canales/Kalman/SMF)


def cfg(name, fallback):
    return getattr(trading_defaults, name, fallback)


# ══════════════════════════════════════════════════════════
# Helpers de tiempo (bar_index -> timestamp unix)
# ══════════════════════════════════════════════════════════

def make_bar_to_ts(df: pd.DataFrame):
    """
    Equivalente exacto a la función bar_to_date() que auto_channels.py usa
    dentro de plot_result()/plot_smart_money_flow() para poner fechas en el
    eje X — incluidas las barras "proyectadas" más allá de la última vela
    real (donde se extienden los canales). Acá se devuelve timestamp unix
    (segundos) en vez de un string, para que el frontend pueda ubicar esas
    barras proyectadas en el eje de tiempo del gráfico.
    """
    n_bars = len(df)
    if n_bars >= 2:
        avg_delta = (df["datetime"].iloc[-1] - df["datetime"].iloc[0]) / (n_bars - 1)
    else:
        avg_delta = pd.Timedelta(days=1)
    last_dt = df["datetime"].iloc[-1]

    def bar_to_ts(idx):
        idx = int(round(idx))
        if idx < 0:
            idx = 0
        if idx < n_bars:
            dt = df["datetime"].iloc[idx]
        else:
            dt = last_dt + avg_delta * (idx - (n_bars - 1))
        return int(dt.timestamp())

    return bar_to_ts


# ══════════════════════════════════════════════════════════
# Serialización de un Channel a JSON (misma matemática que draw_channel())
# ══════════════════════════════════════════════════════════

def channel_payload(ch, color, label, bar_to_ts, x_lo, x_end, last_bar, linewidth=2):
    """
    IMPORTANTE: se manda UN PUNTO POR CADA BARRA REAL (no solo el par de
    puntos inicio/fin), porque lightweight-charts NO espacia su eje de
    tiempo de forma proporcional al calendario para velas diarias — usa
    espaciado por índice (salta fines de semana). Si le mandás una línea
    de 2 puntos con marcas de tiempo "reales" (con huecos de fin de
    semana), la recta que traza en PANTALLA entre esos 2 puntos NO cae
    exactamente sobre el mismo trazado que ch.base_at() calcula puramente
    en espacio de índice de barra — el segundo pivote queda "tocado" por
    el punto pero la línea que pasa por ahí se desvía un poco.

    Mandando un punto por cada índice de barra real (con la MISMA marca de
    tiempo exacta que usa la vela en esa posición), la línea queda
    perfectamente alineada sin importar cómo espacie el eje la librería,
    porque comparte exactamente los mismos timestamps que las velas.

    NO se dibuja el tramo proyectado a futuro (más allá de la última vela
    real): ese tramo usa un espaciado promedio (avg_delta) distinto al
    espaciado real con huecos de fin de semana del tramo histórico, y la
    unión entre ambos espaciados se ve como un quiebre/deformación justo
    en la vela actual — además de tapar las velas más recientes, que son
    las que más importa poder ver. La línea termina limpia en la última
    vela real.
    """
    if ch is None:
        return None
    x_start = max(ch.x1, x_lo)   # recorta el punto de inicio al tramo visible (como el xlim del PNG)

    base_points = [{"t": bar_to_ts(i), "y": float(ch.base_at(i))} for i in range(x_start, last_bar + 1)]

    par_points = [{"t": p["t"], "y": p["y"] + ch.offset} for p in base_points]
    mid_points = [{"t": p["t"], "y": p["y"] + ch.offset / 2} for p in base_points]

    return {
        "label": label,
        "color": color,
        "direction": ch.direction,
        "quality": round(float(ch.quality), 4),
        "linewidth": linewidth,   # 2 = canal largo, 1.6 = canal mediano/corto (igual que draw_channel() en auto_channels.py)
        "base_points": base_points,
        "par_points": par_points,
        "mid_points": mid_points,
        # Los 2 pivotes REALES que definen la línea base del canal (exactamente
        # el high/low de esas 2 velas) — se mandan aparte para poder marcarlos
        # en el gráfico y verificar a simple vista que la línea sí los toca.
        "pivot1": {"t": bar_to_ts(ch.x1), "y": float(ch.y1)},
        "pivot2": {"t": bar_to_ts(ch.x2), "y": float(ch.y2)},
    }


# ══════════════════════════════════════════════════════════
# Serialización del Kalman Flow (segmentos coloreados por tendencia,
# igual que la LineCollection de trend_colors() en plot_result())
# ══════════════════════════════════════════════════════════

def kalman_payload(kres, x_lo, last_bar, bar_to_ts, up_color, down_color, neutral_color="#787B86"):
    trend = kres.trend.to_numpy()
    level = kres.level.to_numpy()

    segments = []
    cur_color, cur_pts = None, []
    for i in range(x_lo, last_bar + 1):
        t = int(trend[i])
        color = up_color if t == 1 else (down_color if t == -1 else neutral_color)
        if color != cur_color:
            if cur_pts:
                segments.append({"color": cur_color, "points": cur_pts})
                cur_pts = [cur_pts[-1]]  # empalma con el segmento anterior (sin huecos)
            cur_color = color
        cur_pts.append({"time": bar_to_ts(i), "value": float(level[i])})
    if cur_pts:
        segments.append({"color": cur_color, "points": cur_pts})

    flips_up, flips_down = [], []
    fu_idx = np.where(kres.flip_up.to_numpy()[x_lo:last_bar + 1])[0] + x_lo
    fd_idx = np.where(kres.flip_down.to_numpy()[x_lo:last_bar + 1])[0] + x_lo
    for i in fu_idx:
        flips_up.append({"time": bar_to_ts(int(i)), "value": float(level[i])})
    for i in fd_idx:
        flips_down.append({"time": bar_to_ts(int(i)), "value": float(level[i])})

    return {"segments": segments, "flips_up": flips_up, "flips_down": flips_down}


# ══════════════════════════════════════════════════════════
# Pipeline principal: MISMA secuencia que el bloque main() de
# auto_channels.py, solo que en vez de argparse usa config.py como
# defaults, con overrides desde el HTML (símbolo, timeframe, fecha).

# ══════════════════════════════════════════════════════════
# Cuántas velas hacen falta REALMENTE para el análisis en vivo
# ══════════════════════════════════════════════════════════

def live_bars_needed():
    """
    Tope de velas que el análisis puede llegar a usar: los canales miran
    hacia atrás hasta CHANNEL_LOOKBACK/_MED/_SHORT, el Kalman hasta
    KALMAN_LOOKBACK, y en pantalla se muestran PLOT_LAST velas. Pedir más
    de eso solo haría todo más lento sin cambiar el resultado. (iq_client
    además lo limita con IQ_VELAS, por defecto 1500, para que "Cargar" sea
    rápido: IQ Option entrega máximo 1000 velas por pedido.)
    """
    lookbacks = [
        cfg("PLOT_LAST", 900),
        cfg("EXTEND_BARS", 150),
        cfg("CHANNEL_LOOKBACK", 6000) or 0,
        cfg("CHANNEL_LOOKBACK_MED", 4000) or 0,
        cfg("CHANNEL_LOOKBACK_SHORT", 3000) or 0,
        cfg("KALMAN_LOOKBACK", None) or (cfg("PLOT_LAST", 900) * 3),
    ]
    return max(lookbacks) + 500   # margen de seguridad


def _long_channel(data, incremental, auto_pivot, show_both):
    pivot_len = cfg("PIVOT_LEN", 21)
    base_kw = dict(atr_len=cfg("ATR_LEN", 14), min_channel_bars=cfg("MIN_BARS", 10),
                    max_channel_bars=cfg("MAX_BARS", 400), quality_th=cfg("QUALITY", 0.55),
                    recent_n=cfg("RECENT_N", 8), lookback_pairs=cfg("LOOKBACK_PAIRS", 5))
    lookback = cfg("CHANNEL_LOOKBACK", None) or None

    if auto_pivot:
        cands = ac._pivot_len_candidates(cfg("PIVOT_LEN_RANGE", (10, 40, 2)))
        kw = dict(base_kw)
        if incremental:
            kw["replace_ratio"] = cfg("REPLACE_RATIO", 0.7)
        best_len, _, _ = ac.search_best_pivot_len(data, cands, incremental, kw,
                                                    lookback=lookback, both_directions=show_both)
        if best_len is not None:
            pivot_len = best_len

    if incremental:
        up_ch, dn_ch, sig, pivots = ac.simulate_incremental(
            data, pivot_len=pivot_len, replace_ratio=cfg("REPLACE_RATIO", 0.7),
            progress=False, lookback=lookback, **base_kw)
    else:
        up_ch, dn_ch, sig, pivots = ac.run_auto_channels(data, pivot_len=pivot_len, **base_kw)

    if not show_both:
        long_ch = ac.select_best_channel(up_ch, dn_ch)
        if long_ch is not None and long_ch.direction == "up":
            up_ch, dn_ch = long_ch, None
        elif long_ch is not None:
            up_ch, dn_ch = None, long_ch
        else:
            up_ch, dn_ch = None, None

    return up_ch, dn_ch, sig, pivots


def _extra_channel(data, incremental, auto_pivot, show_both, prefix, defaults, colors):
    """
    Reproduce el bloque 'Canal mediano' / 'Canal corto' de main(), que solo
    difieren en el prefijo de las claves de config.py y los colores. `prefix`
    es "MED" o "SHORT"; `defaults` son los fallback de cada clave.
    """
    pivot_len = cfg(f"PIVOT_LEN_{prefix}", defaults["pivot_len"])
    base_kw = dict(
        atr_len=cfg(f"ATR_LEN_{prefix}", defaults["atr_len"]),
        min_channel_bars=cfg(f"MIN_BARS_{prefix}", defaults["min_bars"]),
        max_channel_bars=cfg(f"MAX_BARS_{prefix}", defaults["max_bars"]),
        quality_th=cfg(f"QUALITY_{prefix}", defaults["quality"]),
        recent_n=cfg(f"RECENT_N_{prefix}", defaults["recent_n"]),
        lookback_pairs=cfg(f"LOOKBACK_PAIRS_{prefix}", defaults["lookback_pairs"]),
    )
    lookback = cfg(f"CHANNEL_LOOKBACK_{prefix}", None) or None
    replace_ratio = cfg(f"REPLACE_RATIO_{prefix}", defaults["replace_ratio"])

    if auto_pivot:
        cands = ac._pivot_len_candidates(cfg(f"PIVOT_LEN_{prefix}_RANGE", defaults["range"]))
        kw = dict(base_kw)
        if incremental:
            kw["replace_ratio"] = replace_ratio
        best_len, _, _ = ac.search_best_pivot_len(data, cands, incremental, kw,
                                                    lookback=lookback, both_directions=show_both)
        if best_len is not None:
            pivot_len = best_len

    if incremental:
        up_ch, dn_ch, _, _ = ac.simulate_incremental(
            data, pivot_len=pivot_len, replace_ratio=replace_ratio,
            progress=False, lookback=lookback, **base_kw)
    else:
        up_ch, dn_ch, _, _ = ac.run_auto_channels(data, pivot_len=pivot_len, **base_kw)

    extra = []
    up_color, dn_color = colors
    if show_both:
        if up_ch is not None:
            extra.append((up_ch, up_color, f"{defaults['label']} ascendente"))
        if dn_ch is not None:
            extra.append((dn_ch, dn_color, f"{defaults['label']} descendente"))
    else:
        best = ac.select_best_channel(up_ch, dn_ch)
        if best is not None:
            color = up_color if best.direction == "up" else dn_color
            extra.append((best, color, f"{defaults['label']} {'ascendente' if best.direction == 'up' else 'descendente'}"))
    return extra


MEDIUM_DEFAULTS = dict(pivot_len=18, atr_len=10, min_bars=8, max_bars=100, quality=0.45,
                        recent_n=12, lookback_pairs=8, replace_ratio=0.7,
                        range=(6, 30, 2), label="Canal mediano")
SHORT_DEFAULTS = dict(pivot_len=6, atr_len=8, min_bars=4, max_bars=80, quality=0.5,
                       recent_n=15, lookback_pairs=10, replace_ratio=0.7,
                       range=(3, 15, 1), label="Canal corto")


def _limpiar_nan(o):
    """Reemplaza NaN / +-inf por None (-> null en JSON), recursivamente.

    Python serializa float('nan') como NaN, que NO es JSON válido: el
    navegador falla en `await res.json()` aunque el servidor haya
    respondido 200, y el front lo muestra como "No se pudo conectar al
    servidor". Esto se aplica al payload final de compute_analysis().
    """
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _limpiar_nan(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_limpiar_nan(v) for v in o]
    return o


def compute_analysis(symbol=None, timeframe_override=None, hasta=None, auto_pivot_override=None):
    # `hasta` se conserva en la firma por compatibilidad con el contrato de la
    # API original, pero se IGNORA: este proyecto no tiene Backtesting, el
    # gráfico siempre llega hasta la última vela en vivo de IQ Option.
    symbol = (symbol or "").strip()
    timeframe = (timeframe_override or iq_client.TIMEFRAME_DEFECTO).upper()
    if not symbol:
        return {"error": "Elige una divisa en el buscador."}

    try:
        data, source = iq_client.fetch_candles(symbol, timeframe, cantidad=live_bars_needed())
    except iq_client.IqError as e:
        return {"error": str(e)}
    symbol = data.attrs.get("nombre_exacto", symbol.upper())

    if len(data) < 5:
        return {"error": "No hay suficientes velas para ese activo/temporalidad."}

    incremental = bool(cfg("INCREMENTAL", True))
    show_both = bool(cfg("SHOW_BOTH_DIRECTIONS", True))
    # auto_pivot_override viene del checkbox "Pivot automático" del HTML
    # (True/False explícito). Si no se manda nada (None), se usa el default
    # de config.py como hasta ahora — así el checkbox no rompe nada para
    # quien no lo toque.
    auto_pivot = bool(cfg("PIVOT_LEN_AUTO", False)) if auto_pivot_override is None else bool(auto_pivot_override)

    up_ch, dn_ch, sig, pivots = _long_channel(data, incremental, auto_pivot, show_both)

    kalman_result = None
    if cfg("KALMAN_ENABLED", True):
        kalman_result = ac.compute_kalman_windowed(
            data, lookback=(cfg("KALMAN_LOOKBACK", None) or None),
            sensitivity=cfg("KALMAN_SENSITIVITY", 4.0),
            mad_multp=cfg("KALMAN_MAD_MULTP", 1.65),
            mad_multn=cfg("KALMAN_MAD_MULTN", 1.0),
            vol_len=cfg("KALMAN_VOL_LEN", 50),
        )
        sig = ac.combine_with_kalman(sig, kalman_result, len(data) - 1)

    channels_extra = []
    if cfg("SHOW_MEDIUM_CHANNEL", False):
        channels_extra += _extra_channel(data, incremental, auto_pivot, show_both, "MED",
                                          MEDIUM_DEFAULTS, ("#42A5F5", "#FFEE58"))
    if cfg("SHOW_SHORT_CHANNEL", False):
        channels_extra += _extra_channel(data, incremental, auto_pivot, show_both, "SHORT",
                                          SHORT_DEFAULTS, ("#FFA726", "#AB47BC"))

    smf_res = None
    if cfg("SMART_MONEY_FLOW_ENABLED", False) and smf is not None:
        try:
            smf_res = smf.smart_money_flow(
                data,
                momentum_channel_period=cfg("SMF_MOMENTUM_PERIOD", 10),
                trend_period=cfg("SMF_TREND_PERIOD", 21),
                mfi_period=cfg("SMF_MFI_PERIOD", 14),
                signal_smoothing=cfg("SMF_SIGNAL_SMOOTHING", 4),
                pivot_left_bars=cfg("SMF_PIVOT_LEFT", 3),
                pivot_right_bars=cfg("SMF_PIVOT_RIGHT", 3),
                pivot_sensitivity=cfg("SMF_PIVOT_SENSITIVITY", None),
                early_sensitivity=cfg("SMF_EARLY_SENSITIVITY", None),
                calibrate_percentile=cfg("SMF_CALIBRATE_PERCENTILE", 80.0),
            )
        except ValueError as e:
            sig["smf_warning"] = str(e)

    # ── Serialización ──
    plot_last = cfg("PLOT_LAST", 900) or None
    extend_bars = cfg("EXTEND_BARS", 150)
    last_bar = len(data) - 1
    x_end = last_bar + extend_bars
    x_lo = max(0, last_bar - plot_last) if plot_last else 0

    # Si algún canal activo tiene su pivote de origen (x1) más atrás que la
    # ventana de PLOT_LAST velas, ampliamos x_lo hacia atrás para que ESE
    # pivote real quede dentro de lo visible — si no, la línea del canal
    # "flota" en pantalla sin que se vea el punto exacto donde toca el
    # high/low real de la vela (aunque matemáticamente sí lo toca, fuera
    # de cámara). No cambia ningún cálculo, solo el recorte de qué mostrar.
    all_channels_raw = [c for c in (up_ch, dn_ch) if c is not None] + \
                        [c for c, _, _ in channels_extra if c is not None]
    if all_channels_raw:
        min_channel_x1 = min(c.x1 for c in all_channels_raw)
        x_lo = min(x_lo, max(0, min_channel_x1))

    # Red de seguridad: x_lo NUNCA puede quedar en un rango que deje 0 velas
    # para mostrar (por más raro/extremo que sea el caso). Si por lo que sea
    # terminara siendo inválido, mostramos igual las últimas velas en vez de
    # devolver un gráfico vacío sin avisar.
    x_lo = max(0, min(x_lo, last_bar))

    bar_to_ts = make_bar_to_ts(data)

    draw = data.iloc[x_lo:]
    if len(draw) == 0:
        x_lo = max(0, last_bar - min(300, last_bar))
        draw = data.iloc[x_lo:]

    candles = [
        {"time": int(row.datetime.timestamp()), "open": float(row.open),
         "high": float(row.high), "low": float(row.low), "close": float(row.close)}
        for row in draw.itertuples()
    ]

    channels_json = []
    for ch, color, label in (
        (up_ch, "#00E676", "Canal largo ascendente"),
        (dn_ch, "#FF5252", "Canal largo descendente"),
    ):
        j = channel_payload(ch, color, label, bar_to_ts, x_lo, x_end, last_bar, linewidth=2)
        if j:
            channels_json.append(j)
    for ch, color, label in channels_extra:
        j = channel_payload(ch, color, label, bar_to_ts, x_lo, x_end, last_bar, linewidth=1.6)
        if j:
            channels_json.append(j)

    kalman_json = None
    if kalman_result is not None and not cfg("HIDE_KALMAN_PLOT", False):
        kalman_json = kalman_payload(kalman_result, x_lo, last_bar, bar_to_ts,
                                      up_color="#30FDCF", down_color="#E117B7")

    pivots_json = []
    if not cfg("HIDE_PIVOTS", True):
        hi_idx, hi_val, lo_idx, lo_val = pivots
        pivots_json = (
            [{"time": bar_to_ts(i), "value": float(v), "type": "high"} for i, v in zip(hi_idx, hi_val) if i >= x_lo] +
            [{"time": bar_to_ts(i), "value": float(v), "type": "low"} for i, v in zip(lo_idx, lo_val) if i >= x_lo]
        )

    smf_json = None
    if smf_res is not None:
        wave = smf_res.composite.to_numpy()[x_lo:last_bar + 1]
        smooth = smf_res.smoothed.to_numpy()[x_lo:last_bar + 1]
        times = [bar_to_ts(i) for i in range(x_lo, last_bar + 1)]
        bars = [{"time": t, "value": float(w) if not np.isnan(w) else 0.0,
                 "color": "#00E5A0" if (not np.isnan(w) and w > 0) else "#FF5252"}
                for t, w in zip(times, wave)]
        line = [{"time": t, "value": float(s)} for t, s in zip(times, smooth) if not np.isnan(s)]
        smf_json = {"bars": bars, "smoothed": line}

    # ── Tablero S-P-N: para cada uno de los 4 canales (largo, largo
    # inverso, corto, corto inverso), estado (Soporte/Resistencia/Neutro),
    # distancia % al borde relevante y si ya rompió. Reutiliza los MISMOS
    # objetos Channel ya calculados arriba. Si "Corto" está desactivado
    # (SHOW_SHORT_CHANNEL=False), esas dos filas simplemente no aparecen. ──
    posicion_json = []
    if cbs is not None:
        short_up = next((c for c, _color, _lbl in channels_extra
                          if _lbl.startswith("Canal corto") and c.direction == "up"), None)
        short_down = next((c for c, _color, _lbl in channels_extra
                            if _lbl.startswith("Canal corto") and c.direction == "down"), None)
        canales_tablero = [
            ("Canal largo", up_ch),
            ("Canal largo inverso", dn_ch),
            ("Canal corto", short_up),
            ("Canal corto inverso", short_down),
        ]
        resultados = cbs.evaluar_todos_spn(
            data, canales_tablero,
            atr_length=cfg("BREAKOUT_ATR_LEN", 14),
            min_displacement_atr=cfg("BREAKOUT_MIN_DISPLACEMENT", 0.15),
            near_threshold_pct=cfg("SPN_NEAR_THRESHOLD_PCT", 30.0),
        )
        for r in resultados:
            posicion_json.append({
                "label": r.label,
                "estado_label": cbs.ESTADO_LABELS.get(r.estado, r.estado),
                "estado_color": cbs.ESTADO_COLORS.get(r.estado, "#848e9c"),
                "distancia_pct": r.distancia_pct,
                "rotura": r.rotura,
                "rotura_label": "Sí" if r.rotura else "No",
                "rotura_color": cbs.ROTURA_COLOR_SI if r.rotura else cbs.ROTURA_COLOR_NO,
                "lado_rotura": r.lado_rotura,
                "quality": r.quality,
                "confirmacion": {
                    "precio_actual": r.precio_actual,
                    "borde_top": r.borde_top,
                    "borde_bottom": r.borde_bottom,
                    "distancia_exacta_pct": r.distancia_exacta_pct,
                    "cuerpo_vela": r.cuerpo_vela,
                    "displacement": r.displacement,
                    "displacement_minimo": r.displacement_minimo,
                    "atr": r.atr,
                },
            })

    return _limpiar_nan({
        "symbol": symbol,
        "timeframe": timeframe,
        "source": source,
        "candles": candles,
        "channels": channels_json,
        "kalman": kalman_json,
        "pivots": pivots_json,
        "smf": smf_json,
        "posicion_canales": posicion_json,
        "signals": dict(sig),
        "n_candles_total": len(data),
        "digits": data.attrs.get("digits"),
        "auto_pivot": auto_pivot,
        "last_date": data["datetime"].iloc[-1].strftime("%Y-%m-%d %H:%M"),
        "first_date": data["datetime"].iloc[0].strftime("%Y-%m-%d %H:%M"),
    })
