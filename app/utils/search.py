from __future__ import annotations

import re
from typing import Any, List, Optional

from sqlalchemy import func

_norm_re = re.compile(r"[^A-Za-z0-9]+")


def normalize_pn(s: str) -> str:
    """MAYÚSCULAS y sin separadores (solo A-Z 0-9)."""
    if not s:
        return ""
    return _norm_re.sub("", str(s).upper().strip())


def normalize_part_number(code: str):
    """Retorna (full, root) para guardar en DB."""
    if code is None:
        return "", ""
    code = str(code).strip()
    if not code:
        return "", ""
    first_token = code.split()[0] if code.split() else code
    root = first_token.split("-")[0] if first_token else ""
    return code, root


def smart_prefix(term: str, min_len: int = 6) -> str:
    if not term:
        return ""
    t = term.strip()
    return t if len(t) <= min_len else t[:min_len]


def wildcard_x_match(pattern: str, value: str) -> bool:
    p = normalize_pn(pattern)
    v = normalize_pn(value)
    if not p or not v or len(p) != len(v):
        return False
    for pc, vc in zip(p, v):
        if pc == "X":
            if not vc.isalnum():
                return False
            continue
        if pc != vc:
            return False
    return True


def sql_normalize(col, db):
    """Normaliza en SQL quitando separadores."""
    dialect = db.bind.dialect.name
    base = func.upper(func.coalesce(col, ""))

    if dialect == "postgresql":
        return func.regexp_replace(base, r"[^A-Z0-9]", "", "g")

    if dialect == "sqlite":
        x = base
        for ch in ["-", " ", ".", "/", "_", "(", ")", "[", "]"]:
            x = func.replace(x, ch, "")
        return x

    return base


def json_safe_number(v: Any) -> Any:
    """Evita NaN/Inf en respuestas JSON."""
    try:
        import math

        if v is None:
            return None
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
    except Exception:
        pass
    return v


def extract_alias_codes(raw: Optional[str]) -> List[str]:
    if raw is None:
        return []
    s = str(raw).strip()
    if not s or s.lower() == "nan":
        return []

    chunks = [c.strip() for c in re.split(r"[,\n;]+", s) if c and c.strip()]
    out: List[str] = []
    for c in chunks:
        m = re.match(r"^(.*?)\((.*?)\)$", c)
        if m:
            a = m.group(1).strip()
            b = m.group(2).strip()
            if a:
                out.append(a)
            if b:
                out.append(b)
        else:
            out.append(c)

    seen = set()
    deduped = []
    for x in out:
        if x in seen:
            continue
        seen.add(x)
        deduped.append(x)
    return deduped
