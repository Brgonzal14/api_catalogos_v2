@echo off
cd /d "%~dp0"

echo ============================================
echo  Instalar Catalogos Hansair en este PC
echo ============================================
echo.
echo Este script:
echo   1. Abre Docker Desktop
echo   2. Carga las imagenes desde archivos locales
echo   3. Restaura la base de datos (si existe)
echo   4. Inicia la aplicacion
echo.
pause

REM ── 1) Abrir Docker Desktop y esperar ─────────────────────────────────────
echo Abriendo Docker Desktop...
start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"

echo Esperando a que Docker inicie (puede tardar 1-2 minutos la primera vez)...
:wait_docker
docker info >nul 2>&1
if errorlevel 1 (
    timeout /t 5 >nul
    goto wait_docker
)
echo Docker listo.
echo.

REM ── 2) Cargar imagen de la API ─────────────────────────────────────────────
echo [1/4] Cargando imagen catalogos-api...
if exist "%~dp0catalogos-api.tar" (
    docker load -i "%~dp0catalogos-api.tar"
    echo     OK
) else (
    echo     ERROR: no se encontro catalogos-api.tar
    echo     Asegurate de haber copiado todos los archivos del pendrive
    pause
    exit /b 1
)

REM ── 3) Cargar imagen de postgres (si existe, evita descargar de internet) ──
echo [2/4] Cargando imagen postgres:16...
if exist "%~dp0postgres-16.tar" (
    docker load -i "%~dp0postgres-16.tar"
    echo     OK
) else (
    echo     No se encontro postgres-16.tar, se descargara de internet...
)

REM ── 4) Levantar contenedores (crea volumen db_data vacio) ──────────────────
echo [3/4] Iniciando contenedores...
docker compose -f "%~dp0docker-compose.prod.yml" up -d
if errorlevel 1 (
    echo ERROR al levantar contenedores
    pause
    exit /b 1
)

REM Esperar que postgres este listo antes de restaurar datos
echo Esperando que la base de datos este lista...
timeout /t 15 >nul

REM ── 5) Restaurar datos de la base (si existe el backup) ───────────────────
echo [4/4] Restaurando base de datos...
if exist "%~dp0db_data.tar.gz" (
    REM Detener API mientras restauramos para evitar conflictos
    docker compose -f "%~dp0docker-compose.prod.yml" stop api

    REM Restaurar datos en el volumen
    docker run --rm ^
      -v catalogos_hansair_db_data:/target ^
      -v "%~dp0":/backup ^
      alpine sh -c "cd /target && tar xzf /backup/db_data.tar.gz"

    if errorlevel 1 (
        echo ADVERTENCIA: no se pudieron restaurar los datos.
        echo La aplicacion iniciara con base de datos vacia.
    ) else (
        echo     OK - datos restaurados
    )

    REM Reiniciar API
    docker compose -f "%~dp0docker-compose.prod.yml" start api
    echo Esperando que la API arranque...
    timeout /t 8 >nul
) else (
    echo     No se encontro db_data.tar.gz - iniciando con base de datos vacia
    timeout /t 5 >nul
)

REM ── 6) Abrir navegador ─────────────────────────────────────────────────────
start "" "http://localhost:8000/"
start "" "http://localhost:8000/docs"

echo.
echo ============================================
echo  Instalacion completada!
echo.
echo  La API esta disponible en:
echo    http://localhost:8000
echo    http://localhost:8000/docs  (documentacion)
echo.
echo  Para iniciar/detener en el futuro:
echo    Doble clic en instalar_en_nuevo_pc.bat  <- inicia
echo    Doble clic en stop.bat                  <- detiene
echo ============================================
pause