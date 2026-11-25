from iqoptionapi.stable_api import IQ_Option
import csv
import os
import threading

from credenciales import EMAIL, PASSWORD


def conectar():
    """Conecta a IQ Option en cuenta PRACTICE y devuelve el objeto api."""
    api = IQ_Option(EMAIL, PASSWORD)
    print("Intentando conectar...")
    resultado, razon = api.connect()
    print("Estado:", resultado)
    print("Respuesta:", razon)

    if not resultado:
        raise SystemExit("No se pudo conectar. Revisa credenciales.py")

    api.change_balance("PRACTICE")
    print("Saldo de practica:", api.get_balance())
    return api


def con_timeout(funcion, segundos, *args):
    """Ejecuta funcion(*args) y si tarda mas de 'segundos' la abandona.
    Devuelve (valor, error). error es None si todo salio bien."""
    salida = {}

    def correr():
        try:
            salida["valor"] = funcion(*args)
        except Exception as e:
            salida["error"] = repr(e)

    hilo = threading.Thread(target=correr, daemon=True)
    hilo.start()
    hilo.join(segundos)

    if hilo.is_alive():
        return None, f"TIMEOUT ({segundos}s)"
    if "error" in salida:
        return None, salida["error"]
    return salida.get("valor"), None


MONEDAS = {
    "USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD", "NOK", "SEK",
    "PLN", "TRY", "ZAR", "MXN", "BRL", "SGD", "HKD", "THB", "INR", "RUB",
    "CNH", "CNY", "COP", "CLP", "ARS", "BOB", "DOP",
}


# ============================================================
# PRUEBA ACTUAL: diagnostico de la libreria + lista interna
# de activos (no depende de los servidores de IQ Option)
# ============================================================
def prueba(api):
    # --- 1. Que libreria tienes realmente
    print("\n[1] Libreria instalada")
    import iqoptionapi
    try:
        from importlib.metadata import version
        print("Version:", version("iqoptionapi"))
    except Exception as e:
        print("Version: no se pudo leer", repr(e))
    print("Ruta:", os.path.dirname(iqoptionapi.__file__))

    # --- 2. Lista interna de activos que conoce la libreria
    print("\n[2] Activos que la libreria conoce (lista interna)")
    import iqoptionapi.constants as OP_code
    todos = sorted(OP_code.ACTIVES.items())
    print("Total:", len(todos))

    pares = []
    for nombre, id_activo in todos:
        base = nombre.replace("-OTC", "").replace("-op", "")
        if len(base) == 6 and base[:3] in MONEDAS and base[3:] in MONEDAS:
            pares.append(nombre)

    print(f"Pares de divisas encontrados: {len(pares)}")
    for nombre in pares:
        print(" ", nombre)

    with open("activos_conocidos.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["activo", "id", "es_par_divisas"])
        for nombre, id_activo in todos:
            w.writerow([nombre, id_activo, "si" if nombre in pares else "no"])
    print("\nGuardado: activos_conocidos.csv")

    # --- 3. Que metodos tiene esta version para forex/cfd/crypto/digital
    print("\n[3] Metodos disponibles en esta version")
    for nombre in ("get_instruments", "get_all_open_time",
                   "get_digital_underlying_list_data", "get_all_profit",
                   "get_all_init_v2"):
        print(f"  {nombre:<36} {'si' if hasattr(api, nombre) else 'NO'}")


if __name__ == "__main__":
    api = None
    try:
        api = conectar()
        prueba(api)
    except SystemExit as e:
        print(e)
    except Exception as error:
        print("Error:", repr(error))
    finally:
        try:
            api.api.close()
        except Exception:
            pass
        # Fuerza el cierre aunque queden hilos de la libreria colgados
        os._exit(0)