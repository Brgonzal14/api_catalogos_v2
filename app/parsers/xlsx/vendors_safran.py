"""
Parser específico para catálogos SAFRAN SCG (Safran Cabin Germany).

Aplica a archivos con los CAGE codes CH550 y D2249.

Estructura del Excel:
  - Fila 0: texto de aviso que incluye el CAGE code (ej. "CH550" o "D2249")
  - Filas 1-8: vacías
  - Fila 9: cabeceras -> Partnumber | Safran Stock Ref. | Description |
                         Lead time | UOM | Price | Currency | Comments
  - Desde fila 10: datos de piezas (1 fila por pieza)

Campos extraídos:
  - part_number  <- Partnumber
  - description  <- Description
  - price        <- Price
  - currency     <- Currency (columna explícita, normalmente EUR)
  - lead_time    <- Lead time  (atributo)
  - uom          <- UOM        (atributo)
  - safran_stock_ref <- Safran Stock Ref. (atributo / alias)
  - comments     <- Comments   (atributo, incluye MOQ si aplica)
  - min_qty_default  <- extraído de Comments si tiene "MOQ XX EA"
"""
from __future__ import annotations

import io
import re
from typing import Any, Dict, List, Optional

import pandas as pd

from ..registry import register
from ..types import ParseContext, ParseResult
from ...transform.parse_utils import normalize_part_number, parse_price_value


# ── helpers ────────────────────────────────────────────────────────────────────

def _norm(c: Any) -> str:
    """Normaliza un nombre de columna a minúsculas sin espacios extra."""
    return re.sub(r"\s+", " ", str(c).replace("\xa0", " ")).strip().lower()


def _find_header_row(df_raw: pd.DataFrame) -> int:
    """Devuelve el índice de la fila que contiene 'partnumber' (case-insensitive)."""
    for i, row in df_raw.iterrows():
        for cell in row.values:
            if isinstance(cell, str) and "partnumber" in cell.lower().replace(" ", ""):
                return int(i)
    return 9  # fallback: sabemos que siempre es la fila 9


def _extract_cage_code(df_raw: pd.DataFrame) -> str:
    """Lee el texto de la fila 0 y extrae el CAGE code (CH550 / D2249 / …)."""
    try:
        text = str(df_raw.iloc[0, 0])
        # Busca patrón tipo CAGE CODE "XXXXX"
        m = re.search(r'cage code["\s]+([A-Z0-9]{3,8})', text, re.IGNORECASE)
        if m:
            return m.group(1).upper()
    except Exception:
        pass
    return ""


def _parse_moq_from_comments(comments: Any) -> Optional[int]:
    """Extrae MOQ desde un string como 'MOQ 100 EA'."""
    if comments is None:
        return None
    s = str(comments).strip()
    if not s or s.lower() in ("nan", "none"):
        return None
    m = re.search(r"moq\s+(\d+)", s, re.IGNORECASE)
    return int(m.group(1)) if m else None


# ── parser class ───────────────────────────────────────────────────────────────

class SafranSCGXlsxParser:
    """Parser para listas de precios de Safran Cabin Germany (CAGE CH550 y D2249)."""

    name = "xlsx_safran_scg"
    exts = (".xlsx", ".xls")

    # Palabras clave que se usan para detectar el archivo
    _SAFRAN_SIGNALS = ("safran", "ch550", "d2249", "scg")

    def sniff(self, ctx: ParseContext, head: bytes) -> int:
        score = 0
        fn = (ctx.filename or "").lower()
        sup = (ctx.supplier_name or "").lower()

        for kw in self._SAFRAN_SIGNALS:
            if kw in fn:
                score += 30
            if kw in sup:
                score += 30

        # El archivo siempre tiene "Safran Stock Ref." en el encabezado
        if b"Safran Stock Ref" in head or b"safran stock ref" in head.lower():
            score += 40

        # Comprobación básica de columnas características
        if b"Partnumber" in head:
            score += 10

        return min(score, 100)

    def parse(self, file_bytes: bytes, ctx: ParseContext) -> ParseResult:
        return _parse_safran_scg(file_bytes, ctx)


register(SafranSCGXlsxParser())


# ── core parse function ────────────────────────────────────────────────────────

def _parse_safran_scg(file_bytes: bytes, ctx: ParseContext) -> ParseResult:
    # 1. Leer sin header para detectar fila de encabezado y CAGE code
    raw = pd.read_excel(io.BytesIO(file_bytes), header=None, engine="openpyxl")

    cage_code = _extract_cage_code(raw)
    header_row_idx = _find_header_row(raw)

    # 2. Releer con el header correcto
    df = pd.read_excel(
        io.BytesIO(file_bytes),
        header=header_row_idx,
        engine="openpyxl",
    )

    # 3. Mapear columnas por nombre normalizado
    col_map: Dict[str, str] = {}
    for col in df.columns:
        k = _norm(col)
        if k == "partnumber" or k == "part number":
            col_map[col] = "part_number"
        elif "safran stock" in k or "stock ref" in k:
            col_map[col] = "safran_stock_ref"
        elif k == "description":
            col_map[col] = "description"
        elif "lead time" in k or k == "lead":
            col_map[col] = "lead_time"
        elif k == "uom":
            col_map[col] = "uom"
        elif k == "price":
            col_map[col] = "price"
        elif k == "currency":
            col_map[col] = "currency"
        elif k == "comments":
            col_map[col] = "comments"
        else:
            col_map[col] = k  # columnas extra se conservan tal cual

    df = df.rename(columns=col_map)

    # 4. Filtrar filas sin part_number válido
    if "part_number" not in df.columns:
        raise ValueError(
            "SAFRAN SCG: no se encontró la columna 'Partnumber' en el archivo."
        )

    df = df[df["part_number"].notna()]
    df = df[df["part_number"].astype(str).str.strip().ne("")]
    df = df[
        ~df["part_number"]
        .astype(str)
        .str.strip()
        .str.lower()
        .isin(["partnumber", "part number", "nan", "none"])
    ]
    df = df.reset_index(drop=True)

    # 5. Construir listas de salida
    parts_rows: List[Dict[str, Any]] = []
    tiers_rows: List[Dict[str, Any]] = []
    alias_rows: List[Dict[str, Any]] = []
    attr_rows: List[Dict[str, Any]] = []

    source_file = ctx.filename or ""

    for _, row in df.iterrows():
        pn_raw = row.get("part_number")
        if pn_raw is None:
            continue
        pn_str = re.sub(r"\s+", "", str(pn_raw).strip())
        if not pn_str or pn_str.lower() in ("nan", "none"):
            continue

        # Normalizar part number
        part_number_full, part_number_root = normalize_part_number(pn_str)

        # Descripción
        desc_raw = row.get("description")
        description = (
            str(desc_raw).strip()
            if desc_raw is not None and str(desc_raw).strip().lower() not in ("nan", "none", "")
            else None
        )

        # Precio y moneda
        price_raw = row.get("price")
        price = parse_price_value(price_raw)

        currency_raw = row.get("currency")
        if currency_raw and str(currency_raw).strip().lower() not in ("nan", "none", ""):
            currency = str(currency_raw).strip().upper()
        else:
            # fallback: leer del nombre del archivo
            currency = "EUR" if "eur" in source_file.lower() else "USD"

        # MOQ desde comments
        comments_raw = row.get("comments")
        moq = _parse_moq_from_comments(comments_raw)
        min_qty = moq if moq and moq > 0 else 1

        # Campos extra como atributos
        lead_time_raw = row.get("lead_time")
        uom_raw = row.get("uom")
        safran_ref_raw = row.get("safran_stock_ref")

        # ── parts ──
        parts_rows.append(
            {
                "part_number": part_number_full,
                "part_number_root": part_number_root,
                "description": description,
                "currency": currency,
                "base_price": price,
                "min_qty_default": min_qty,
                "source_file": source_file,
                "source_sheet": "Pricelist",
                "cage_code": cage_code or None,
                "parser_name": SafranSCGXlsxParser.name,
            }
        )

        # ── tiers (un único tramo por pieza) ──
        if price is not None:
            tiers_rows.append(
                {
                    "part_number": part_number_full,
                    "min_qty": min_qty,
                    "max_qty": None,
                    "unit_price": price,
                    "currency": currency,
                    "source_file": source_file,
                    "source_sheet": "Pricelist",
                }
            )

        # ── alias: Safran Stock Ref. ──
        if safran_ref_raw is not None:
            ref_str = str(safran_ref_raw).strip()
            if ref_str and ref_str.lower() not in ("nan", "none", "") and ref_str != part_number_full:
                alias_rows.append(
                    {
                        "part_number": part_number_full,
                        "alias_code": ref_str,
                        "source": "SAFRAN_STOCK_REF",
                        "source_file": source_file,
                    }
                )

        # ── atributos ──
        extras = {
            "lead_time": lead_time_raw,
            "uom": uom_raw,
            "comments": comments_raw,
            "cage_code": cage_code or None,
        }
        for k, v in extras.items():
            if v is None:
                continue
            v_str = str(v).strip()
            if not v_str or v_str.lower() in ("nan", "none"):
                continue
            attr_rows.append(
                {
                    "part_number": part_number_full,
                    "key": k,
                    "value": v_str,
                    "source_file": source_file,
                    "source_sheet": "Pricelist",
                }
            )

    parts_df = pd.DataFrame(parts_rows) if parts_rows else pd.DataFrame()
    tiers_df = pd.DataFrame(tiers_rows) if tiers_rows else pd.DataFrame()
    aliases_df = pd.DataFrame(alias_rows) if alias_rows else pd.DataFrame()
    attrs_df = pd.DataFrame(attr_rows) if attr_rows else pd.DataFrame()

    return ParseResult(
        parts=parts_df,
        tiers=tiers_df,
        aliases=aliases_df,
        attributes=attrs_df,
        parser_name=SafranSCGXlsxParser.name,
    )