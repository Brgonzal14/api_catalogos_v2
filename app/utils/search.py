from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session


# -----------------------------
# Normalización de PN
# -----------------------------
_PN_KEEP = re.compile(r"[^A-Za-z0-9]+")


def normalize_pn(s: str) -> str:
    """Normaliza un PN a formato comparable: solo A-Z0-9, uppercase."""
    return _PN_KEEP.sub("", (s or "").upper()).strip()


def normalize_part_number(pn: str) -> tuple[str, str]:
    """Devuelve (full, root). Root = prefijo hasta antes del primer separador '-' si existe."""
    pn = (pn or "").strip()
    if not pn:
        return "", ""
    full = pn.strip()
    # root: antes del primer '-' si existe, si no, primeras letras/números “base”
    if "-" in full:
        root = full.split("-", 1)[0].strip()
    else:
        # toma bloque inicial alfanumérico
        m = re.match(r"^[A-Za-z0-9]+", full)
        root = m.group(0) if m else full
    return full, root


# -----------------------------
# SQL helpers
# -----------------------------
def sql_normalize(col, db: Session):
    """
    Normaliza una columna SQL (Postgres) a comparable: upper + remover no alfanumérico.
    """
    # regexp_replace(col, '[^A-Za-z0-9]+', '', 'g')
    return func.upper(func.regexp_replace(col, r"[^A-Za-z0-9]+", "", "g"))


def json_safe_number(v: Any):
    """Convierte tipos numpy/decimal a tipos JSON-safe."""
    try:
        # int/float normal
        if v is None:
            return None
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return v
        # cosas tipo Decimal / numpy
        s = str(v)
        if s.lower() in ("nan", "none", "null"):
            return None
        if "." in s:
            return float(s)
        return int(s)
    except Exception:
        return None


# -----------------------------
# Wildcard X helpers (compat con tu lógica)
# -----------------------------
def smart_prefix(norm: str, min_len: int = 6) -> str:
    norm = normalize_pn(norm)
    if len(norm) < min_len:
        return ""
    return norm[:min_len]


def wildcard_x_match(candidate: str, query: str) -> bool:
    """
    Match simple donde 'X' en candidate actúa como wildcard (un char).
    Ej: ABX12 matchea AB912
    """
    cand = normalize_pn(candidate)
    q = normalize_pn(query)
    if not cand or not q:
        return False
    if len(cand) != len(q):
        return False
    for c, t in zip(cand, q):
        if c == "X":
            continue
        if c != t:
            return False
    return True