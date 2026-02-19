from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from .parse_utils import (
    clean_multiline_cell,
    normalize_part_number,
    normalize_pn,
    parse_price_value,
)


def _norm_col(c: Any) -> str:
    s = str(c).replace("\xa0", " ").replace("\n", " ").replace("\r", " ")
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def _dedupe_columns(cols: Iterable[str]) -> List[str]:
    seen: Dict[str, int] = {}
    out: List[str] = []
    for c in cols:
        if c not in seen:
            seen[c] = 1
            out.append(c)
        else:
            seen[c] += 1
            out.append(f"{c}__{seen[c]}")
    return out


def extract_alias_codes(value: Any) -> List[str]:
    """Extrae posibles códigos desde una celda.

    Maneja:
      - "AAA (BBB)" -> [AAA, BBB]
      - "AAA, BBB; CCC\nDDD" -> [AAA, BBB, CCC, DDD]
      - Ignora "-" / vacíos.
    """
    if value is None:
        return []
    s = str(value).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return []
    if s.strip() == "-":
        return []

    # AAA (BBB)
    m = re.match(r"^\s*([^\s(]+)\s*\(([^)]+)\)\s*$", s)
    if m:
        a = m.group(1).strip()
        b = m.group(2).strip()
        return [x for x in [a, b] if x and x != "-"]

    # split general
    parts = re.split(r"[;,\n\t]+", s)
    out: List[str] = []
    for p in parts:
        p = p.strip().strip(",")
        if not p or p == "-":
            continue
        out.append(p)
    return out


def _safe_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        if isinstance(v, float) and pd.isna(v):
            return None
    except Exception:
        pass
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return None
    s = s.replace(".", "").replace(",", ".")
    m = re.search(r"[-+]?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        f = float(m.group(0))
        if f <= 0:
            return None
        return int(f) if float(f).is_integer() else int(round(f))
    except Exception:
        return None


def standardize_pdf_df(df: pd.DataFrame, default_currency: str = "USD") -> pd.DataFrame:
    """Normaliza nombres de columnas (sin perder columnas extra)."""
    if df is None or df.empty:
        return pd.DataFrame()

    colmap: Dict[str, str] = {}
    for c in df.columns:
        k = _norm_col(c)
        mapped = k
        if k in (
            "part number",
            "pn",
            "p/n",
            "part_no",
            "part-no",
            "material",
            "material number",
            "material no",
            "pnr",
            "item number",
            "mpn",
            "art.-nr",
            "art.nr",
            "bauteil",
            "bauteil-nr",
            "bauteil nr",
            "article no",
            "articleno",
            "current albany pn",
        ):
            mapped = "part_number"
        elif "description" in k or k in ("bezeichnung", "item description", "designation", "conditioning unit"):
            mapped = "description"
        elif k in (
            "net price",
            "sales price",
            "unit price",
            "price",
            "vk",
            "eur/unit",
            "eur / unit",
            "price 2026",
            "price 2025",
            "price (usd)",
        ) or ("price" in k and "list" not in k):
            mapped = "price"
        elif "currency" in k or k in ("cur", "curr"):
            mapped = "currency"
        elif "unit of measure" in k or k in ("uom", "unit", "unt", "sales unit"):
            mapped = "unit_code"
        elif ("lead" in k and "time" in k) or k in ("ltm", "leadtime"):
            mapped = "lead_time"
        elif k in ("moq", "minimum order qty", "minimum order quantity", "minimum order qty.") or (
            "minimum" in k and "qty" in k
        ):
            mapped = "min_qty"
        elif k in ("min qty", "from qty", "menge", "ab menge", "min buy"):
            mapped = "min_qty"
        elif k in ("max qty", "to qty"):
            mapped = "max_qty"
        elif "pricing note" in k or ("note" in k) or ("remark" in k) or ("comment" in k):
            mapped = "pricing_note"
        elif "supplier" in k and ("p/n" in k or "pn" in k or "part" in k):
            mapped = "supplier_part_no"
        elif k == "previous albany pn":
            mapped = "alias_previous"
        elif "boeing" in k and "pn" in k:
            mapped = "alias_boeing"

        colmap[c] = mapped

    out = df.rename(columns=colmap).copy()
    out.columns = _dedupe_columns([str(c) for c in out.columns])

    # limpieza de texto clave
    for c in ("part_number", "description", "pricing_note"):
        if c in out.columns:
            out[c] = out[c].map(clean_multiline_cell)

    # currency default
    if "currency" not in out.columns:
        out["currency"] = default_currency
    else:
        out["currency"] = out["currency"].fillna("")
        out.loc[out["currency"].astype(str).str.strip().eq(""), "currency"] = default_currency

    # price float
    if "price" in out.columns:
        out["price"] = out["price"].map(parse_price_value)

    # min/max qty ints (si existen)
    if "min_qty" in out.columns:
        out["min_qty"] = out["min_qty"].map(_safe_int)
    if "max_qty" in out.columns:
        out["max_qty"] = out["max_qty"].map(_safe_int)

    # normalize part_number and drop empties
    if "part_number" in out.columns:
        out["part_number"] = out["part_number"].astype(str).str.strip()
        out.loc[out["part_number"].isin(["", "nan", "None"]), "part_number"] = None
        out = out.dropna(subset=["part_number"]).reset_index(drop=True)

    return out


def build_parse_result_from_df(
    df_raw: pd.DataFrame,
    *,
    source_file: str,
    default_currency: str = "USD",
    parser_name: str = "",
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Convierte DF crudo (PDF) en (parts, tiers, aliases, attributes)."""
    df = standardize_pdf_df(df_raw, default_currency=default_currency)
    if df is None or df.empty:
        return (pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame())

    # Detectar modo tiers
    has_tier_cols = ("min_qty" in df.columns) and ("price" in df.columns) and df["min_qty"].notna().any()
    many_rows_per_part = df.duplicated(subset=["part_number"]).any()
    has_max = ("max_qty" in df.columns) and df["max_qty"].notna().any()
    is_tiers = bool(has_tier_cols and (many_rows_per_part or has_max))

    # Aliases (auto)
    alias_cols: List[str] = []
    for c in df.columns:
        if c.startswith("alias_"):
            alias_cols.append(c)
        elif c in ("supplier_part_no", "article_no", "rf_part_number", "standard_part_number"):
            alias_cols.append(c)

    # Attributes (todo lo que no es core ni tier ni alias)
    core_cols = {
        "part_number",
        "description",
        "price",
        "currency",
        "unit_code",
        "lead_time",
        "min_qty",
        "max_qty",
        "pricing_note",
    }
    ignore_cols = set(alias_cols) | core_cols
    extra_cols = [c for c in df.columns if c not in ignore_cols]

    # Tiers df
    tiers_df = pd.DataFrame()
    if is_tiers:
        # max_qty es opcional
        if "max_qty" not in df.columns:
            df = df.copy()
            df["max_qty"] = None
        tiers_df = df[["part_number", "min_qty", "max_qty", "price", "currency"]].copy()
        tiers_df = tiers_df.rename(columns={"price": "unit_price"})
        tiers_df["source_file"] = source_file
        tiers_df = tiers_df.dropna(subset=["unit_price"]).reset_index(drop=True)

    # Parts df (agregación)
    def _first_non_null(series: Optional[pd.Series]):
        if series is None:
            return None
        for v in series:
            if v is None:
                continue
            try:
                if isinstance(v, float) and pd.isna(v):
                    continue
            except Exception:
                pass
            if isinstance(v, str) and v.strip().lower() in ("nan", "none", "null", ""):
                continue
            return v
        return None

    parts_rows: List[Dict[str, Any]] = []
    for pn, g in df.groupby("part_number", dropna=True):
        pn_full, pn_root = normalize_part_number(pn)

        currency = _first_non_null(g.get("currency")) or default_currency
        description = _first_non_null(g.get("description"))
        unit_code = _first_non_null(g.get("unit_code"))
        lead_time = _first_non_null(g.get("lead_time"))
        pricing_note = _first_non_null(g.get("pricing_note"))

        if is_tiers:
            base_price = None
            try:
                prices = [p for p in g["price"].tolist() if p is not None]
                base_price = min(prices) if prices else None
            except Exception:
                base_price = _first_non_null(g.get("price"))

            min_qty_default = None
            try:
                qs = [q for q in g["min_qty"].tolist() if q is not None]
                min_qty_default = min(qs) if qs else None
            except Exception:
                min_qty_default = _first_non_null(g.get("min_qty"))
        else:
            base_price = _first_non_null(g.get("price"))
            min_qty_default = _first_non_null(g.get("min_qty"))

        parts_rows.append(
            {
                "part_number": pn_full,
                "part_number_root": pn_root,
                "description": (str(description).strip() if description is not None else None),
                "currency": (str(currency).upper().strip() if currency is not None else default_currency),
                "base_price": base_price,
                "min_qty_default": int(min_qty_default) if isinstance(min_qty_default, int) else (min_qty_default or 1),
                "pricing_note": (str(pricing_note).strip() if pricing_note is not None else None),
                "unit_code": (str(unit_code).strip() if unit_code is not None else None),
                "lead_time": (str(lead_time).strip() if lead_time is not None else None),
                "source_file": source_file,
                "parser": parser_name,
            }
        )

    parts_df = pd.DataFrame(parts_rows)

    # Aliases df
    alias_rows: List[Dict[str, Any]] = []
    for _, r in df.iterrows():
        pn = r.get("part_number")
        if not pn:
            continue
        pn_norm = normalize_pn(pn)
        for c in alias_cols:
            for code in extract_alias_codes(r.get(c)):
                if not code:
                    continue
                if normalize_pn(code) == pn_norm:
                    continue
                alias_rows.append(
                    {
                        "part_number": pn,
                        "alias_code": code,
                        "source": c,
                        "source_file": source_file,
                    }
                )
    aliases_df = pd.DataFrame(alias_rows) if alias_rows else pd.DataFrame()
    if not aliases_df.empty:
        aliases_df = aliases_df.drop_duplicates(subset=["part_number", "alias_code"]).reset_index(drop=True)

    # Attributes df
    attr_rows: List[Dict[str, Any]] = []
    for _, r in df.iterrows():
        pn = r.get("part_number")
        if not pn:
            continue
        for c in extra_cols:
            v = r.get(c)
            if v is None:
                continue
            try:
                if isinstance(v, float) and pd.isna(v):
                    continue
            except Exception:
                pass
            sv = str(v).strip()
            if not sv or sv.lower() in ("nan", "none", "null"):
                continue
            if len(sv) > 400:
                continue
            attr_rows.append(
                {
                    "part_number": pn,
                    "key": str(c),
                    "value": sv,
                    "source_file": source_file,
                }
            )
    attrs_df = pd.DataFrame(attr_rows) if attr_rows else pd.DataFrame()

    return parts_df, tiers_df, aliases_df, attrs_df
