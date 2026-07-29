@echo off
REM Kronos Studio: banco de pruebas de estrategias.
REM --server.headless evita que Streamlit se quede esperando un correo por consola.
cd /d "%~dp0"
echo Abriendo Kronos Studio en http://localhost:8502
python -m streamlit run dashboard\estudio.py --server.port 8502 --server.headless true --browser.gatherUsageStats false
if errorlevel 1 (
    echo.
    echo Fallo al arrancar. Comprueba que streamlit este instalado:
    echo    pip install -r requirements-dashboard.txt
    pause
)
