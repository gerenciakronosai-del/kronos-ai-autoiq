@echo off
REM Lanzador del panel de Kronos. Doble clic para abrirlo.
REM
REM Notas de por que el comando es tan largo:
REM   * "python -m streamlit" y no "streamlit": el ejecutable no queda en el
REM     PATH al instalar desde la Python de la Microsoft Store.
REM   * "--server.headless true": sin esto Streamlit pide un email la primera
REM     vez y se queda BLOQUEADO esperando, sin arrancar el servidor.
REM   * "--browser.gatherUsageStats false": no manda telemetria.

cd /d "%~dp0"

echo ============================================
echo   KRONOS AI - AutoIQ
echo ============================================
echo.

if "%ANTHROPIC_API_KEY%"=="" (
    echo  [!] ANTHROPIC_API_KEY no esta definida.
    echo      El panel arrancara solo con el cerebro local.
    echo      Para activar la IA:  setx ANTHROPIC_API_KEY "sk-ant-..."
    echo      y despues cierra y vuelve a abrir esta ventana.
) else (
    echo  [OK] ANTHROPIC_API_KEY detectada. Cerebro IA disponible.
)
echo.

REM Libera el puerto 8501 si quedo un panel anterior colgado.
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8501" ^| findstr "LISTENING"') do (
    echo  [i] Cerrando panel anterior en el puerto 8501 ^(PID %%a^)...
    taskkill /F /PID %%a >nul 2>&1
)

echo  Abriendo http://localhost:8501 ...
echo  Cierra esta ventana para detener el panel.
echo.

python -m streamlit run dashboard/app.py ^
    --server.port 8501 ^
    --server.headless true ^
    --browser.gatherUsageStats false

pause
