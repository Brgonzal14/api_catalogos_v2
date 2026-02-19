from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.exc import OperationalError

from app.db import Base, engine
from app.api.routes import catalogs_router, parts_router, stats_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    max_retries = 10
    wait_seconds = 2
    for attempt in range(1, max_retries + 1):
        try:
            print(f"[lifespan] Intentando crear tablas (intento {attempt}/{max_retries})...")
            Base.metadata.create_all(bind=engine)
            print("[lifespan] Tablas creadas correctamente.")
            break
        except OperationalError as e:
            print(f"[lifespan] BD no disponible todavía: {e}")
            if attempt == max_retries:
                raise
            await asyncio.sleep(wait_seconds)

    yield
    print("[lifespan] Cerrando aplicación.")


app = FastAPI(title="API Catálogos Aeronáuticos", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# Frontend estático
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/", include_in_schema=False)
def read_frontend():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


# =========================
# Routers
# =========================
app.include_router(catalogs_router)
app.include_router(parts_router)
app.include_router(stats_router)
