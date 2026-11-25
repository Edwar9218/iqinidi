"""
Configuración de Django para iq-grafico.

Proyecto mínimo: SOLO el gráfico, sin login, sin base de datos, sin Celery.
Las preferencias (indicadores encendidos) y los dibujos se guardan en el
navegador (localStorage); las velas vienen de IQ Option (core/engine/iq_client.py).
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Solo para uso local en tu PC: esta clave no protege nada importante porque
# no hay sesiones ni usuarios. NO expongas este servidor a internet.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "iq-grafico-solo-uso-local")

DEBUG = os.environ.get("DJANGO_DEBUG", "True").strip().lower() in ("1", "true", "yes")

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "chartview",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Sin base de datos: nada de esto necesita guardar nada en el servidor.
DATABASES = {}

LANGUAGE_CODE = "es"
TIME_ZONE = "America/Bogota"
USE_I18N = False
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
