@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  === Grafico IQ Option - indicadores en vivo ===
echo.

if not exist ".venv\Scripts\activate.bat" (
  echo Creando entorno virtual .venv ...
  python -m venv .venv
  if errorlevel 1 (
    echo ERROR: no se pudo crear el entorno virtual. Verifica que Python este instalado.
    pause
    exit /b 1
  )
)

call ".venv\Scripts\activate.bat"

python -c "import django, pandas, numpy" 2>nul || (
  echo Instalando Django, pandas y numpy...
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
)
python -c "import iqoptionapi" 2>nul || (
  echo Instalando iqoptionapi desde GitHub ^(necesita git^)...
  python -m pip install git+https://github.com/iqoptionapi/iqoptionapi.git
)
python -c "import iqoptionapi" 2>nul || (
  echo ERROR: no se pudo instalar iqoptionapi. Instala git y vuelve a ejecutar.
  pause
  exit /b 1
)

echo.
echo  Abre http://localhost:8000 (se abre solo en 3 segundos).
echo  Para cerrar: Ctrl+C en esta ventana.
echo.
start "" /min cmd /c "timeout /t 3 >nul & start http://localhost:8000"
python manage.py runserver 127.0.0.1:8000 --noreload
pause
