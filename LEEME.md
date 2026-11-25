# Gráfico IQ Option — indicadores en vivo

Solo la pestaña **Gráfico** del proyecto (canales, Kalman, KN TP/SL, S/R Avanzado, S/R Fractal,
Secuencia, TZT, dibujos), alimentada por IQ Option en lugar de MT5. **Sin backtesting** (el código
sigue ahí pero oculto). **Solo lee precios: no opera ni manda órdenes.**

## Cómo correrlo
1. Abre `credenciales.py` y pon tu correo y contraseña de IQ Option (y tu `UTC_OFFSET_HORAS`; Colombia = -5).
2. Doble clic en `arrancar.bat`: crea el entorno virtual `.venv`, lo activa, instala lo necesario y arranca.
   (A mano, en PowerShell: `python -m venv .venv` → `.\.venv\Scripts\Activate.ps1` →
   `pip install -r requirements.txt` → `pip install git+https://github.com/iqoptionapi/iqoptionapi.git` →
   `python manage.py runserver --noreload`.)
3. Abre http://localhost:8000. Al hacer clic en **Activo** aparece el buscador con todo lo que IQ ofrece.

## Qué esperar
- Buscador: filtros Divisas/Todos, Real/OTC y «Solo abiertos»; muestra el % de pago y si está abierto.
  Escribe parte del nombre (ej. `eur`), ↑/↓ y Enter.
- El precio se mueve en vivo (consulta cada 0,5 s). Los indicadores de navegador se recalculan cada ~3 s
  y al abrirse una vela; canales y Kalman (servidor) se refrescan al abrirse una vela, máx. cada 60 s.
- Temporalidades: S15, S30, M1, M2, M5, M10, M15, M30, H1, H2, H4, H8, H12, D1, W1.
- Solo binarias/turbo (incluye OTC). Digital, forex/CFD y cripto de IQ no se pudieron leer con esta librería.
- Los indicadores y dibujos se guardan en el navegador (localStorage).
- El eje de tiempo muestra UTC + `UTC_OFFSET_HORAS`.
