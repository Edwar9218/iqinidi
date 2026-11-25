from django.urls import path

from . import views

app_name = "chartview"

urlpatterns = [
    path("", views.grafico, name="grafico"),
    path("api/datos/", views.api_datos, name="api_datos"),
    path("api/activos/", views.api_activos, name="api_activos"),
    path("api/vela_actual/", views.api_vela_actual, name="api_vela_actual"),
    path("api/velas_extra/", views.api_velas_extra, name="api_velas_extra"),
    path("api/velas_despues/", views.api_velas_despues, name="api_velas_despues"),
    path("api/hora_servidor/", views.api_hora_servidor, name="api_hora_servidor"),
    path("api/config/", views.api_config, name="api_config"),
    path("api/ping/", views.api_ping, name="api_ping"),
]
