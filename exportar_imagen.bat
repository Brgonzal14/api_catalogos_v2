@echo off
cd /d "%~dp0"

echo ============================================
echo  Exportar Catalogos Hansair a pendrive/USB
echo ============================================

REM Pedir ruta de destino (pendrive o carpeta)
set /p DEST="Ingresa la ruta de destino (ej: E:\catalogos_hansair): "

if "%DEST%"=="" (
    echo ERROR: debes ingresar una ruta
    pause
    exit /b 1
)

REM Crear carpeta destino si no existe
if not exist "%DEST%" mkdir "%DEST%"

echo.
echo Destino: %DEST%
echo.

REM ── 1) Exportar imagen de la API ──────────────────────────────────────────
echo [1/4] Exportando imagen catalogos-api...
docker save catalogos-api:latest -o "%DEST%\catalogos-api.tar"
if errorlevel 1 (
    echo ERROR: no se pudo exportar la imagen.
    echo Asegurate de haber ejecutado start_prod.bat al menos una vez.
    pause
    exit /b 1
)
echo     OK - catalogos-api.tar

REM ── 2) Exportar imagen de postgres (para no necesitar internet) ───────────
echo [2/4] Exportando imagen postgres:16...
docker save postgres:16 -o "%DEST%\postgres-16.tar"
if errorlevel 1 (
    echo ADVERTENCIA: no se pudo exportar postgres. El PC destino necesitara internet.
) else (
    echo     OK - postgres-16.tar
)

REM ── 3) Exportar volumen de la base de datos (datos existentes) ────────────
echo [3/4] Exportando datos de la base de datos...
docker run --rm ^
  -v api_catalogos_v2_db_data:/source ^
  -v "%DEST%":/backup ^
  alpine tar czf /backup/db_data.tar.gz -C /source .
if errorlevel 1 (
    echo ADVERTENCIA: no se pudo exportar la base de datos.
    echo El PC destino empezara con una base de datos vacia (normal en instalacion nueva).
) else (
    echo     OK - db_data.tar.gz
)

REM ── 4) Copiar archivos necesarios del proyecto ────────────────────────────
echo [4/4] Copiando archivos del proyecto...
xcopy /E /I /Y "%~dp0db"                  "%DEST%\db\"             >nul
copy /Y "%~dp0docker-compose.prod.yml"    "%DEST%\docker-compose.prod.yml"  >nul
copy /Y "%~dp0instalar_en_nuevo_pc.bat"   "%DEST%\instalar_en_nuevo_pc.bat" >nul
copy /Y "%~dp0stop.bat"                   "%DEST%\stop.bat"        >nul
echo     OK - archivos copiados

echo.
echo ============================================
echo  Exportacion completa!
echo.
echo  Contenido en %DEST%:
echo    catalogos-api.tar    imagen de la API
echo    postgres-16.tar      imagen de PostgreSQL
echo    db_data.tar.gz       datos de la base de datos
echo    db\                  scripts de inicializacion
echo    docker-compose.prod.yml
echo    instalar_en_nuevo_pc.bat
echo    stop.bat
echo.
echo  Lleva esa carpeta al nuevo PC y ejecuta:
echo    instalar_en_nuevo_pc.bat
echo ============================================
pause