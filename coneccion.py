from iqoptionapi.stable_api import IQ_Option
import getpass
import time

print("IQ OPTION - PRUEBA DE CONEXION")

email = "edwad9218@hotmail.com"
password = "Ed2315972*"

api = IQ_Option(email, password)

print("\nIntentando conectar...")

try:
    resultado, razon = api.connect()

    print("Estado:", resultado)
    print("Respuesta:", razon)

    if resultado:
        print("\nConexion establecida")

        print("WebSocket:", api.check_connect())

        api.change_balance("PRACTICE")

        print("Saldo de practica:", api.get_balance())

        # ---------------------------------------------------------
        # LISTADO DE TODOS LOS ACTIVOS DE TODOS LOS TIPOS DE OPCION
        # ---------------------------------------------------------
        print("\nObteniendo activos disponibles (puede tardar unos segundos)...")

        api.update_ACTIVES_OPCODE()          # actualiza la lista de activos
        abiertos = api.get_all_open_time()   # dict por tipo de opcion
        ganancias = api.get_all_profit()     # % de pago por activo

        # Cambia a True si quieres ver solo los activos abiertos ahora
        SOLO_ABIERTOS = False

        total = 0
        for tipo, activos in abiertos.items():
            print(f"\n===== {tipo.upper()} ({len(activos)} activos) =====")
            for nombre, datos in sorted(activos.items()):
                if SOLO_ABIERTOS and not datos.get("open"):
                    continue

                estado = "ABIERTO" if datos.get("open") else "cerrado"

                pago = ""
                if tipo in ("binary", "turbo") and nombre in ganancias:
                    p = ganancias[nombre].get(tipo)
                    if p:
                        pago = f" | pago: {round(p * 100)}%"

                print(f"{nombre:<20} {estado}{pago}")
                total += 1

        print(f"\nTotal mostrado: {total}")

        # ---------------------------------------------------------
        # PRUEBA DE VELAS
        # ---------------------------------------------------------
        print("\nSolicitando velas EURUSD...")

        velas = api.get_candles(
            "EURUSD",
            60,
            5,
            time.time()
        )

        for vela in velas:
            print(
                "Open:", vela["open"],
                "Close:", vela["close"],
                "High:", vela["max"],
                "Low:", vela["min"]
            )

    else:
        print("No se pudo establecer la conexion:", razon)

except Exception as error:
    print("Error:", repr(error))

finally:
    try:
        api.api.close()
    except Exception:
        pass


print("\nObteniendo activos...")
try:
    api.update_ACTIVES_OPCODE()
except Exception as e:
    print("Error en update_ACTIVES_OPCODE:", repr(e))

abiertos = api.get_all_open_time()
print("Tipos recibidos:", list(abiertos.keys()))
for tipo, activos in abiertos.items():
    print(tipo, "->", len(activos), "activos")

# Plan B: lista de activos conocidos por la librería
import iqoptionapi.constants as OP_code
print("\nActivos conocidos por la librería:", len(OP_code.ACTIVES))
for nombre in sorted(OP_code.ACTIVES):
    print(nombre)