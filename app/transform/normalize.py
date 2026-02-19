from __future__ import annotations
import re
from typing import Any, Dict, Optional
import pandas as pd

from .parse_utils import clean_multiline_cell, parse_price_value

def normalize_raw_df(df: pd.DataFrame, default_currency: str = "USD") -> pd.DataFrame:
    """Normaliza un DF crudo (PDF tables o PDF vendor) a columnas estándar mínimas.

    Output mínimo:
      - part_number
      - description
      - currency
      - price
      - unit_code (opcional)
      - lead_time (opcional)
      - spq (opcional)
      - moq (opcional)
      - notes (opcional)
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["part_number","description","currency","price","unit_code","lead_time","spq","moq","notes"])

    # 1) limpiar nombres
    def norm_col(c: Any) -> str:
        s = str(c).replace("\xa0", " ").replace("\n"," ").replace("\r"," ")
        s = re.sub(r"\s+", " ", s).strip().lower()
        return s

    colmap: Dict[str,str] = {}
    for c in df.columns:
        k = norm_col(c)
        mapped = k
        if k in ("part number","pn","p/n","part_no","part-no","material","material number","material no","pnr","item number","mpn","art.-nr","art.nr","bauteil","bauteil-nr","bauteil nr","article no","articleno"):
            mapped = "part_number"
        elif "description" in k or k in ("bezeichnung","item description","designation","conditioning unit"):
            mapped = "description"
        elif k in ("net price","sales price","unit price","price","vk","eur/unit","eur / unit","usd","eur","price 2026","price 2025","net price 2026","net price 2025"):
            mapped = "price"
        elif "currency" in k or k in ("cur","curr"):
            mapped = "currency"
        elif k in ("unt",):
            mapped = "unit_code"
        elif "unit of measure" in k or k in ("uom","unit","sales unit"):
            mapped = "unit_code"
        elif "lead" in k and "time" in k or k in ("ltm","leadtime"):
            mapped = "lead_time"
        elif "standard package" in k or k in ("spq","standard package qty","package qty","package  qty"):
            mapped = "spq"
        elif "minimum order" in k or k in ("moq","minimum order qty","minimum order quantity"):
            mapped = "moq"
        elif "note" in k or "comment" in k:
            mapped = "notes"
        colmap[c] = mapped

    out = df.rename(columns=colmap).copy()

    # 2) si viene como tabla sin header (0..N), intentamos usar primera fila como header
    if all(re.fullmatch(r"\d+", str(c)) for c in out.columns):
        # no sabemos; devolvemos crudo con columna 'part_number' inexistente
        return out

    # 3) limpiar textos multilínea en columnas clave
    for c in ("part_number","description","notes"):
        if c in out.columns:
            out[c] = out[c].map(clean_multiline_cell)

    # 4) currency default
    if "currency" not in out.columns:
        out["currency"] = default_currency
    else:
        out["currency"] = out["currency"].fillna("")
        out.loc[out["currency"].astype(str).str.strip().eq(""), "currency"] = default_currency

    # 5) price float
    if "price" in out.columns:
        out["price"] = out["price"].map(parse_price_value)

    # 6) asegurar columnas mínimas
    for c in ("part_number","description","price","unit_code","lead_time","spq","moq","notes"):
        if c not in out.columns:
            out[c] = None

    # filtrar filas sin part_number
    out["part_number"] = out["part_number"].astype(str).str.strip()
    out.loc[out["part_number"].isin(["", "nan", "None"]) , "part_number"] = None
    out = out.dropna(subset=["part_number"]).reset_index(drop=True)

    return out
