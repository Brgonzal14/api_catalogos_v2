"""
Parser para catálogos FREUDENBERG / NEWMET (Excel).

Estructura del archivo:
  - Fila 0: cabeceras directamente (header=0)
  - Columnas principales:
      PLPartNumber  -> part_number
      Description   -> description
      RPN           -> alias (referencia cruzada / part number alternativo)
      LTM           -> lead_time en meses (atributo)
      MSQ           -> MOQ / min_qty_default
      CUR           -> moneda
      UNP           -> precio unitario base (con margen, precio de venta)
      PBQ1/PBP1     -> tier 1 de precio de venta
      PBQ2/PBP2     -> tier 2 de precio de venta
  - Sección de costos (PBQ1.1..PBP5): se ignora, son precios internos

Notas:
  - Algunas piezas aparecen duplicadas con PLPartNumber == RPN (referencias cruzadas).
    Se deduplicar usando PLPartNumber como clave principal.
  - UNP se usa como base_price; los tiers PBQx/PBPx como tramos adicionales.
  - MSQ = Standard Pack Quantity / MOQ.
"""
from __future__ import annotations

import io
import re
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from ..registry import register
from ..types import ParseContext, ParseResult
from ...transform.parse_utils import normalize_part_number, parse_price_value


# ── helpers ────────────────────────────────────────────────────────────────────

def _notna(v) -> bool:
    try:
        return pd.notna(v)
    except Exception:
        return v is not None


def _clean_str(v: Any) -> Optional[str]:
    if not _notna(v):
        return None
    s = str(v).strip()
    return s if s and s.lower() not in ("nan", "none", "nat") else None


def _to_int(v) -> Optional[int]:
    s = _clean_str(v)
    if not s:
        return None
    try:
        return int(float(s))
    except Exception:
        return None


def _parse_tiers(row: pd.Series, currency: str) -> List[Tuple[int, Optional[int], float]]:
    """
    Lee los pares PBQ1/PBP1, PBQ2/PBP2 de la sección de precios de venta.
    Devuelve lista de (min_qty, max_qty, unit_price) ordenada por min_qty.
    """
    tier_pairs = [
        ("PBQ1", "PBP1"),
        ("PBQ2", "PBP2"),
    ]
    raw_tiers = []
    for q_col, p_col in tier_pairs:
        q = _to_int(row.get(q_col))
        p = parse_price_value(row.get(p_col))
        if q is not None and p is not None:
            raw_tiers.append((q, p))

    if not raw_tiers:
        return []

    # Ordenar por cantidad ascendente y calcular max_qty
    raw_tiers.sort(key=lambda x: x[0])
    result = []
    for i, (min_q, price) in enumerate(raw_tiers):
        if i + 1 < len(raw_tiers):
            max_q = raw_tiers[i + 1][0] - 1
        else:
            max_q = None
        result.append((min_q, max_q, price))
    return result


# ── parser class ───────────────────────────────────────────────────────────────

class FreudenbergXlsxParser:
    """Parser para listas de precios Freudenberg / Newmet (Excel)."""

    name = "xlsx_freudenberg"
    exts = (".xlsx", ".xls")

    _SIGNALS = ("freudenberg", "newmet", "freudenberg___newmet", "freudenb")

    def sniff(self, ctx: ParseContext, head: bytes) -> int:
        score = 0
        fn = (ctx.filename or "").lower()
        sup = (ctx.supplier_name or "").lower()

        for kw in self._SIGNALS:
            if kw in fn:
                score += 40
            if kw in sup:
                score += 40

        # Columna característica del formato interno Freudenberg
        if b"PLPartNumber" in head:
            score += 30
        if b"KEYWORD" in head:
            score += 10

        return min(score, 100)

    def parse(self, file_bytes: bytes, ctx: ParseContext) -> ParseResult:
        return _parse_freudenberg(file_bytes, ctx)


register(FreudenbergXlsxParser())


# ── core parse function ────────────────────────────────────────────────────────

def _parse_freudenberg(file_bytes: bytes, ctx: ParseContext) -> ParseResult:
    df = pd.read_excel(io.BytesIO(file_bytes), header=0, engine="openpyxl")

    # Limpiar nombres de columna
    df.columns = [str(c).strip() for c in df.columns]

    # Validar columna principal
    if "PLPartNumber" not in df.columns:
        raise ValueError("Freudenberg: no se encontró la columna 'PLPartNumber'.")

    # Filtrar filas sin part_number
    df = df[df["PLPartNumber"].notna()]
    df = df[df["PLPartNumber"].astype(str).str.strip().ne("")]
    df = df.reset_index(drop=True)

    source_file = ctx.filename or ""

    parts_rows: List[Dict[str, Any]] = []
    tiers_rows: List[Dict[str, Any]] = []
    alias_rows: List[Dict[str, Any]] = []
    attr_rows: List[Dict[str, Any]] = []

    seen_pns: set = set()  # para deduplicar

    for _, row in df.iterrows():
        pn_raw = _clean_str(row.get("PLPartNumber"))
        if not pn_raw:
            continue

        part_number_full, part_number_root = normalize_part_number(pn_raw)

        # Deduplicar: si ya procesamos este PN, saltamos
        if part_number_full in seen_pns:
            continue
        seen_pns.add(part_number_full)

        # Descripción
        description = _clean_str(row.get("Description")) or _clean_str(row.get("KEYWORD"))

        # Moneda
        currency = _clean_str(row.get("CUR")) or "EUR"
        currency = currency.upper()

        # MOQ (MSQ = Standard Pack Quantity = min_qty)
        moq = _to_int(row.get("MSQ")) or 1

        # Precio base (UNP = precio unitario con margen = precio de venta)
        base_price = parse_price_value(row.get("UNP"))

        # Si UNP no tiene valor, usar el primer tier disponible
        if base_price is None:
            base_price = parse_price_value(row.get("PBP1"))

        # Alias: RPN (referencia cruzada / PN alternativo)
        rpn = _clean_str(row.get("RPN"))

        # Lead time en meses
        ltm = _to_int(row.get("LTM"))

        # UNT (unidad)
        unt = _clean_str(row.get("UNT"))

        # ── parts ──
        parts_rows.append({
            "part_number":      part_number_full,
            "part_number_root": part_number_root,
            "description":      description,
            "currency":         currency,
            "base_price":       base_price,
            "min_qty_default":  moq,
            "source_file":      source_file,
            "source_sheet":     "Tabelle1",
        })

        # ── tiers (tramos de precio de venta PBQ/PBP) ──
        tier_list = _parse_tiers(row, currency)
        if tier_list:
            for min_q, max_q, unit_price in tier_list:
                tiers_rows.append({
                    "part_number": part_number_full,
                    "min_qty":     min_q,
                    "max_qty":     max_q,
                    "unit_price":  unit_price,
                    "currency":    currency,
                    "source_file": source_file,
                    "source_sheet": "Tabelle1",
                })
        elif base_price is not None:
            # Sin tiers explícitos: un único tramo desde MOQ
            tiers_rows.append({
                "part_number": part_number_full,
                "min_qty":     moq,
                "max_qty":     None,
                "unit_price":  base_price,
                "currency":    currency,
                "source_file": source_file,
                "source_sheet": "Tabelle1",
            })

        # ── alias: RPN ──
        if rpn and rpn != part_number_full:
            rpn_full, _ = normalize_part_number(rpn)
            if rpn_full != part_number_full:
                alias_rows.append({
                    "part_number": part_number_full,
                    "alias_code":  rpn,
                    "source":      "RPN",
                    "source_file": source_file,
                })

        # ── atributos ──
        extras: Dict[str, Any] = {
            "lead_time_months": str(ltm) if ltm is not None else None,
            "uom":              unt,
            "moq":              str(moq) if moq > 1 else None,
            "keyword":          _clean_str(row.get("KEYWORD")),
            "mfr":              _clean_str(row.get("MFR")),
            "cond":             _clean_str(row.get("Cond")),
        }
        for k, v in extras.items():
            if not v:
                continue
            attr_rows.append({
                "part_number":  part_number_full,
                "key":          k,
                "value":        v,
                "source_file":  source_file,
                "source_sheet": "Tabelle1",
            })

    parts_df  = pd.DataFrame(parts_rows)  if parts_rows  else pd.DataFrame()
    tiers_df  = pd.DataFrame(tiers_rows)  if tiers_rows  else pd.DataFrame()
    aliases_df = pd.DataFrame(alias_rows) if alias_rows  else pd.DataFrame()
    attrs_df  = pd.DataFrame(attr_rows)   if attr_rows   else pd.DataFrame()

    return ParseResult(
        parts=parts_df,
        tiers=tiers_df,
        aliases=aliases_df,
        attributes=attrs_df,
        parser_name=FreudenbergXlsxParser.name,
    )