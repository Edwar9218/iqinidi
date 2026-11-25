import sys

from django.apps import AppConfig


class ChartviewConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "chartview"

    def ready(self):
        # Conecta con IQ Option en segundo plano apenas arranca el servidor,
        # para que el primer "Cargar" no espere la conexión. Solo con
        # `runserver` (no al correr tests ni otros comandos de manage.py).
        if "runserver" in sys.argv:
            from core.engine import iq_client
            iq_client.precalentar()
