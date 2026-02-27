@echo off
cd /d "%~dp0"

echo ===============================
echo  Catalogos Hansair - PRODUCCION
echo ===============================

REM 1) Abrir Docker Desktop y esperar
echo Abriendo Docker Desktop...
start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"

echo Esperando a que Docker inicie (puede tardar 30-60 seg la primera vez)...
:wait_docker
docker info >nul 2>&1
if errorlevel 1 (
    timeout /t 5 >nul
    goto wait_docker
)
echo Docker listo.

REM 2) Construir la imagen con el codigo sellado dentro
echo.
echo Construyendo imagen catalogos-api...
docker build -t catalogos-api:latest .
if errorlevel 1 (
    echo ERROR al construir la imagen
    pause
    exit /b 1
)
echo Imagen construida correctamente.

REM 3) Levantar contenedores en modo produccion (sin reconstruir)
echo.
echo Levantando contenedores...
docker compose -f docker-compose.prod.yml up -d
if errorlevel 1 (
    echo ERROR al levantar contenedores
    pause
    exit /b 1
)

REM 4) Esperar que la API arranque
echo Esperando que la API este lista...
timeout /t 8 >nul

REM 5) Abrir navegador
start "" "http://localhost:8000/"
start "" "http://localhost:8000/docs"

echo.
echo ========================================
echo  Listo! La API esta corriendo en:
echo  http://localhost:8000
echo  http://localhost:8000/docs (Swagger)
echo ========================================
pause