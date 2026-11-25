"""config/urls.py — la raíz del sitio ES el gráfico."""
from django.urls import include, path

urlpatterns = [
    path("", include("chartview.urls")),
]
