from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func, or_
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
    if "-" in full:
        root = full.split("-", 1)[0].strip()
    else:
        m = re.match(r"^[A-Za-z0-9]+", full)
        root = m.group(0) if m else full
    return full, root


# -----------------------------
# SQL helpers
# -----------------------------
def sql_normalize(col, db: Session | None = None):
    """
    upper + remover no alfanumérico (Postgres regexp_replace).
    `db` es opcional: se acepta solo por compat con llamadas antiguas.
    """
    return func.upper(func.regexp_replace(col, r"[^A-Za-z0-9]+", "", "g"))


def json_safe_number(v: Any):
    """Convierte tipos numpy/decimal a tipos JSON-safe."""
    try:
        if v is None:
            return None
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return v
        s = str(v)
        if s.lower() in ("nan", "none", "null"):
            return None
        if "." in s:
            return float(s)
        return int(s)
    except Exception:
        return None


# -----------------------------
# Wildcard X helpers
# -----------------------------
def smart_prefix(norm: str, min_len: int = 6) -> str:
    norm = normalize_pn(norm)
    if len(norm) < min_len:
        return ""
    return norm[:min_len]


def wildcard_x_match(candidate: str, query: str) -> bool:
    """Match simple donde 'X' en candidate actúa como wildcard (un char)."""
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


# -----------------------------
# NUEVO: filtro para buscar por PN o por alias (END-UNIT)
# -----------------------------
def apply_search_filter(query, db: Session, q: str):
    """
    Aplica filtro PN/alias a un query SQLAlchemy.

    Requiere que el query ya tenga models.Part como entidad base.
    Opcionalmente, si tu endpoint hace join con PartAlias, esto lo aprovecha.
    Si no, igual funciona si le pasas el query con el join.
    """
    from app import models  # import local para evitar ciclos

    qn = normalize_pn(q or "")
    if not qn:
        return query

    # Normalizados SQL
    pn_full_n = sql_normalize(models.Part.part_number_full)
    pn_root_n = sql_normalize(models.Part.part_number_root)

    # Alias join (si no existe join, igual se puede usar con outerjoin)
    alias_n = sql_normalize(models.PartAlias.code)

    # OJO: para que alias_n funcione, el endpoint debe hacer outerjoin a PartAlias
    # query = query.outerjoin(models.PartAlias, models.PartAlias.part_id == models.Part.id)
    return query.filter(
        or_(
            pn_full_n.contains(qn),
            pn_root_n.contains(qn),
            alias_n.contains(qn),
        )
    )