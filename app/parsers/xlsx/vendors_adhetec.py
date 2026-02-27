"""
Parser específico para catálogos ADHETEC.

Estructura del Excel:
  - Filas de encabezado al inicio (título, vacías, etc.)
  - Header real: Customer PN | Designation | Unit of Measure | Standard Lead Time | MOQ | Unit EUR Prices
  - Múltiples filas por part number, una por cada tramo de precio (MOQ distinto)
  - El MOQ de cada fila es el min_qty del tier; el max_qty se deduce del siguiente MOQ - 1
"""
from __future__ import annotations

import io
import re
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from ..registry import register
from ..types import ParseContext, ParseResult
from ...transform.parse_utils import normalize_part_number, parse_price_value


def _find_header_row(df_raw: pd.DataFrame) -> int:
    """Devuelve el índice de la fila que contiene 'customer pn' (case-insensitive)."""
    for i, row in df_raw.iterrows():
        vals = [str(v).strip().lower() for v in row.values]
        if any("customer pn" in v for v in vals):
            return int(i)
    return 0


class AdhetecXlsxParser:
    name = "xlsx_adhetec"
    exts = (".xlsx", ".xls")

    def sniff(self, ctx: ParseContext, head: bytes) -> int:
        name_l = (ctx.filename or "").lower()
        sup_l = (ctx.supplier_name or "").lower()
        if "adhetec" in name_l or "adhetec" in sup_l:
            return 90  # alta prioridad sobre el parser genérico
        return 0

    def parse(self, file_bytes: bytes, ctx: ParseContext) -> ParseResult:
        return parse_adhetec(file_bytes, ctx)


register(AdhetecXlsxParser())


def parse_adhetec(file_bytes: bytes, ctx: ParseContext) -> ParseResult:
    currency = "EUR"  # ADHETEC siempre EUR

    # ── 1. Leer sin header para detectar dónde empieza la tabla ──
    raw = pd.read_excel(io.BytesIO(file_bytes), header=None, engine="openpyxl")
    header_row_idx = _find_header_row(raw)

    # ── 2. Releer con el header correcto ──
    df = pd.read_excel(
        io.BytesIO(file_bytes),
        header=header_row_idx,
        engine="openpyxl",
    )

    # Normalizar nombres de columna
    col_map: Dict[str, str] = {}
    for col in df.columns:
        k = re.sub(r"\s+", " ", str(col).replace("\xa0", " ")).strip().lower()
        if "customer pn" in k:
            col_map[col] = "customer_pn"
        elif "designation" in k:
            col_map[col] = "designation"
        elif "unit of measure" in k or k in ("uom", "unit"):
            col_map[col] = "unit_code"
        elif "lead time" in k or "leadtime" in k:
            col_map[col] = "lead_time"
        elif k == "moq":
            col_map[col] = "moq"
        elif "eur price" in k or "unit eur" in k or "unit price" in k or k == "price":
            col_map[col] = "unit_price"
        else:
            col_map[col] = k  # mantener como atributo extra

    df = df.rename(columns=col_map)

    # Descartar filas sin PN
    df = df[df["customer_pn"].notna()]
    df = df[df["customer_pn"].astype(str).str.strip().ne("")]
    df = df[~df["customer_pn"].astype(str).str.strip().str.lower().isin(
        ["customer pn", "nan", "none"]
    )]
    df = df.reset_index(drop=True)

    # ── 3. Agrupar por PN y construir parts + tiers ──
    parts_rows: List[Dict[str, Any]] = []
    tiers_rows: List[Dict[str, Any]] = []
    attr_rows: List[Dict[str, Any]] = []

    # Columnas que NO se guardan como atributos extra
    _skip_attr = {"customer_pn", "designation", "unit_price", "moq", "lead_time", "unit_code"}

    for pn_raw, group in df.groupby("customer_pn", sort=False):
        pn_str = str(pn_raw).strip()
        if not pn_str or pn_str.lower() in ("nan", "none"):
            continue

        part_number_full, part_number_root = normalize_part_number(pn_str)

        # Descripción: tomar la primera no vacía
        desc = ""
        if "designation" in group.columns:
            for v in group["designation"]:
                s = str(v).strip()
                if s and s.lower() not in ("nan", "none", "-"):
                    desc = s
                    break

        # Atributos extra (unit_code, lead_time, …) — solo de la primera fila
        first_row = group.iloc[0]
        attrs_seen: Dict[str, str] = {}
        for k in group.columns:
            if k in _skip_attr:
                continue
            v = first_row.get(k)
            if v is None or str(v).strip().lower() in ("nan", "none", ""):
                continue
            attrs_seen[str(k)] = str(v).strip()

        # unit_code y lead_time también los guardamos como atributos
        for extra_key in ("unit_code", "lead_time"):
            v = first_row.get(extra_key)
            if v is not None and str(v).strip().lower() not in ("nan", "none", ""):
                attrs_seen[extra_key] = str(v).strip()

        # ── Tiers: ordenar por MOQ ──
        tier_list: List[Tuple[int, Optional[int], float]] = []

        for _, row in group.iterrows():
            raw_moq = row.get("moq")
            raw_price = row.get("unit_price")

            if raw_moq is None or str(raw_moq).strip().lower() in ("nan", "none", ""):
                continue
            if raw_price is None or str(raw_price).strip().lower() in ("nan", "none", ""):
                continue

            try:
                min_q = int(float(str(raw_moq).replace(",", ".")))
            except (ValueError, TypeError):
                continue

            unit_price = parse_price_value(raw_price)
            if unit_price is None:
                continue

            tier_list.append((min_q, unit_price))

        if not tier_list:
            continue

        # Ordenar por min_qty ascendente
        tier_list.sort(key=lambda x: x[0])

        # Calcular max_qty: el siguiente min_qty - 1; el último queda sin límite
        tiers_with_max: List[Tuple[int, Optional[int], float]] = []
        for i, (min_q, price) in enumerate(tier_list):
            if i + 1 < len(tier_list):
                max_q: Optional[int] = tier_list[i + 1][0] - 1
            else:
                max_q = None
            tiers_with_max.append((min_q, max_q, price))

        base_price = tiers_with_max[0][2]
        min_qty_default = tiers_with_max[0][0]

        # ── Guardar part ──
        parts_rows.append({
            "part_number": part_number_full,
            "part_number_root": part_number_root,
            "description": desc or None,
            "currency": currency,
            "base_price": base_price,
            "min_qty_default": min_qty_default,
            "source_file": ctx.filename,
            "source_sheet": "sheet1",
        })

        # ── Guardar tiers ──
        for min_q, max_q, unit_price in tiers_with_max:
            tiers_rows.append({
                "part_number": part_number_full,
                "min_qty": min_q,
                "max_qty": max_q,
                "unit_price": unit_price,
                "currency": currency,
                "source_file": ctx.filename,
                "source_sheet": "sheet1",
            })

        # ── Guardar atributos ──
        for k, v in attrs_seen.items():
            attr_rows.append({
                "part_number": part_number_full,
                "key": k,
                "value": v,
                "source_file": ctx.filename,
                "source_sheet": "sheet1",
            })

    parts_df = pd.DataFrame(parts_rows) if parts_rows else pd.DataFrame()
    tiers_df = pd.DataFrame(tiers_rows) if tiers_rows else pd.DataFrame()
    attrs_df = pd.DataFrame(attr_rows) if attr_rows else pd.DataFrame()

    return ParseResult(
        parts=parts_df,
        tiers=tiers_df,
        aliases=pd.DataFrame(),
        attributes=attrs_df,
        parser_name="xlsx_adhetec",
    )