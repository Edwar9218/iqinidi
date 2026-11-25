"""
chartview/views.py — endpoints del gráfico (todos de LECTURA: nunca se opera).

Mismo contrato que el proyecto original donde el front-end ya lo usaba
(`/api/datos/`, `/api/vela_actual/`, `/api/velas_extra/`…); la diferencia es
que las velas salen de IQ Option y que no hay login, tablero ni backtesting.
"""
from django.http import JsonResponse
from django.shortcuts import render

from core.engine import analysis, iq_client


def _sin_cache(respuesta):
    respuesta["Cache-Control"] = "no-store"
    return respuesta


def grafico(request):
    """La pantalla del gráfico (única página del proyecto)."""
    return render(request, "chartview/grafico.html", {
        "timeframes": list(iq_client.TIMEFRAMES),
        "timeframe_defecto": iq_client.TIMEFRAME_DEFECTO,
    })


def api_datos(request):
    """Velas + canales + Kalman + señales del activo y la temporalidad pedidos."""
    symbol = request.GET.get("symbol", "").strip() or None
    timeframe = request.GET.get("timeframe", "").strip() or None

    auto_pivot = request.GET.get("auto_pivot", "").strip().lower()
    auto_pivot_override = True if auto_pivot in ("1", "true", "si", "sí") \
        else False if auto_pivot in ("0", "false", "no") else None

    try:
        result = analysis.compute_analysis(symbol=symbol, timeframe_override=timeframe,
                                           auto_pivot_override=auto_pivot_override)
    except Exception as e:                        # noqa: BLE001
        return JsonResponse({"error": f"{type(e).__name__}: {e}"}, status=500)

    if "error" in result:
        return _sin_cache(JsonResponse(result, status=400))
    return _sin_cache(JsonResponse(result))


def api_activos(request):
    """Todo lo que IQ Option ofrece (para el buscador de divisas), con estado
    abierto/cerrado y % de pago. `?forzar=1` salta la caché de 45 s."""
    try:
        activos = iq_client.listar_activos(forzar=request.GET.get("forzar") == "1")
    except iq_client.IqError as e:
        return _sin_cache(JsonResponse({"error": str(e)}, status=503))
    except Exception as e:                        # noqa: BLE001
        return _sin_cache(JsonResponse({"error": f"{type(e).__name__}: {e}"}, status=500))
    return _sin_cache(JsonResponse({
        "activos": activos,
        "timeframes": list(iq_client.TIMEFRAMES),
    }))


def api_vela_actual(request):
    """La vela en formación (precio EN VIVO). Liviano: no toca la red, solo lee lo
    que el WebSocket de IQ Option ya dejó en memoria."""
    symbol = request.GET.get("symbol", "").strip()
    timeframe = request.GET.get("timeframe", "").strip()
    if not symbol or not timeframe:
        return JsonResponse({"error": "faltan symbol/timeframe"}, status=400)
    try:
        vela = iq_client.vela_actual(symbol, timeframe)
    except iq_client.IqError as e:
        return _sin_cache(JsonResponse({"error": str(e)}, status=400))
    except Exception as e:                        # noqa: BLE001
        return _sin_cache(JsonResponse({"error": f"{type(e).__name__}: {e}"}, status=500))
    if vela is None:
        return _sin_cache(JsonResponse({"error": "sin datos todavía"}, status=404))
    return _sin_cache(JsonResponse(vela))


def api_velas_extra(request):
    """Botón "Cargar más historial": un lote de velas más viejas que las que el
    navegador ya tiene (sin recalcular canales/Kalman)."""
    symbol = request.GET.get("symbol", "").strip()
    timeframe = request.GET.get("timeframe", iq_client.TIMEFRAME_DEFECTO).strip()
    antes_de = request.GET.get("antes_de", "").strip()
    cantidad = request.GET.get("cantidad", "500").strip()

    if not symbol or not antes_de:
        return JsonResponse({"error": "Faltan parámetros symbol/antes_de."}, status=400)
    try:
        antes_de_ts = int(antes_de)
        cantidad = max(50, min(int(cantidad), 1000))
    except ValueError:
        return JsonResponse({"error": "antes_de/cantidad deben ser números."}, status=400)

    try:
        candles = iq_client.fetch_older(symbol, timeframe, antes_de_ts, cantidad)
    except iq_client.IqError as e:
        return JsonResponse({"error": str(e)}, status=400)
    except Exception as e:                        # noqa: BLE001
        return JsonResponse({"error": f"{type(e).__name__}: {e}"}, status=500)
    return JsonResponse({"candles": candles})


def api_velas_despues(request):
    """Sin Backtesting no hay "velas posteriores": siempre vacío (el front-end
    original todavía conoce este endpoint)."""
    return JsonResponse({"candles": []})


def api_hora_servidor(request):
    """Hora que se muestra en el eje (UTC + desfase configurado)."""
    return _sin_cache(JsonResponse(iq_client.hora_info()))


def api_config(request):
    """Valores por defecto para precargar los selectores."""
    return JsonResponse({"timeframe": iq_client.TIMEFRAME_DEFECTO})


def api_ping(request):
    """Latido del servidor + estado de la conexión con IQ Option (barra de arriba)."""
    return _sin_cache(JsonResponse({
        "status": "ok",
        "iq": iq_client.estado(),
        "utc_offset_horas": iq_client.offset_segundos() / 3600,
    }))
