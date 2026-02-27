"""
Parser para catálogos KEDDEG / Parker Filtration (Excel).

Estructura del archivo:
  - Filas 0-4: metadatos (Rev, Released, Effective date) — se ignoran
  - Fila 5:   cabeceras:
      Part Number | Distributor Net Price | Min Order Qty | Lead-time in weeks |
      OEM Part Number | Description | Application
  - Desde fila 6: datos (1 fila por pieza)
  - Nota: la columna "Distributor Net Price" tiene un espacio inicial

Campos extraídos:
  - part_number      <- Part Number
  - description      <- Description
  - price            <- Distributor Net Price (0 = sin precio)
  - min_qty          <- Min Order Qty
  - lead_time        <- Lead-time in weeks (atributo)
  - oem_part_number  <- OEM Part Number -> aliases (puede contener múltiples PNs separados por comas)
  - application      <- Application (atributo)
  - currency         <- USD (del nombre del archivo)

Notas:
  - Precio = 0 se trata como sin precio (pricing_note = "POA")
  - OEM Part Number puede tener múltiples códigos: "83237-15, 180849-28" -> 2 aliases
  - Las últimas filas contienen notas legales (se filtran por Part Number inválido)
"""
from __future__ import annotations

import io
import re
from typing import Any, Dict, List, Optional

import pandas as pd

from ..registry import register
from ..types import ParseContext, ParseResult
from ...transform.parse_utils import normalize_part_number


# ── helpers ────────────────────────────────────────────────────────────────────

def _clean(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s if s and s.lower() not in ("nan", "none", "nat") else None


def _to_int(v: Any) -> Optional[int]:
    s = _clean(v)
    if not s:
        return None
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def _split_oem_pns(v: Any) -> List[str]:
    """Extrae lista de PNs OEM desde una celda que puede contener múltiples separados por coma/punto y coma/salto."""
    s = _clean(v)
    if not s:
        return []
    # Separar por coma, punto y coma, salto de línea
    parts = re.split(r"[,;\n]+", s)
    result = []
    for p in parts:
        p = p.strip()
        if p and p.lower() not in ("nan", "none", ""):
            result.append(p)
    return result


# ── parser class ───────────────────────────────────────────────────────────────

class KeddegXlsxParser:
    """Parser para listas de precios Keddeg / Parker Filtration (Excel)."""

    name = "xlsx_keddeg"
    exts = (".xlsx", ".xls")

    _SIGNALS = ("keddeg", "parker filtration", "parker_filtration")

    def sniff(self, ctx: ParseContext, head: bytes) -> int:
        score = 0
        fn = (ctx.filename or "").lower()
        sup = (ctx.supplier_name or "").lower()

        for kw in self._SIGNALS:
            if kw in fn:
                score += 50
            if kw in sup:
                score += 50

        # Columna característica del formato Keddeg
        if b"Distributor Net Price" in head:
            score += 30
        if b"OEM Part Number" in head:
            score += 20

        return min(score, 100)

    def parse(self, file_bytes: bytes, ctx: ParseContext) -> ParseResult:
        return _parse_keddeg(file_bytes, ctx)


register(KeddegXlsxParser())


# ── core parse function ────────────────────────────────────────────────────────

def _parse_keddeg(file_bytes: bytes, ctx: ParseContext) -> ParseResult:
    source_file = ctx.filename or ""

    # Header en fila 5 (índice 5, cero-based)
    df = pd.read_excel(io.BytesIO(file_bytes), header=5, engine="openpyxl")

    # Limpiar espacios en nombres de columna
    df.columns = [str(c).strip() for c in df.columns]

    # Validar columna principal
    if "Part Number" not in df.columns:
        raise ValueError("Keddeg: no se encontró la columna 'Part Number'.")

    # Filtrar filas sin part_number válido o con notas legales
    df = df[df["Part Number"].notna()].copy()
    df = df[df["Part Number"].astype(str).str.strip().ne("")].copy()

    # Eliminar filas que parecen notas (Part Number muy largo / contiene espacios y letras)
    def _is_valid_pn(v: Any) -> bool:
        s = str(v).strip()
        # PN válido: alfanumérico con guiones, máximo 20 chars, sin espacios internos largos
        if len(s) > 30:
            return False
        if " " in s:
            return False
        return bool(re.match(r"^[A-Za-z0-9][A-Za-z0-9\-\.]*$", s))

    df = df[df["Part Number"].apply(_is_valid_pn)].reset_index(drop=True)

    # Determinar moneda desde el nombre del archivo
    fname_lower = source_file.lower()
    if "eur" in fname_lower:
        currency = "EUR"
    elif "gbp" in fname_lower:
        currency = "GBP"
    else:
        currency = "USD"

    parts_rows: List[Dict[str, Any]] = []
    alias_rows: List[Dict[str, Any]] = []
    attr_rows:  List[Dict[str, Any]] = []

    for _, row in df.iterrows():
        pn_raw = _clean(row.get("Part Number"))
        if not pn_raw:
            continue

        part_number_full, part_number_root = normalize_part_number(pn_raw)

        # Descripción
        description = _clean(row.get("Description"))

        # Precio (espacio en nombre de columna ya limpiado por strip en columns)
        price_raw = row.get("Distributor Net Price")
        try:
            price = float(price_raw) if price_raw is not None else None
            if price == 0.0:
                price = None  # precio 0 = sin precio publicado
        except (TypeError, ValueError):
            price = None

        # MOQ
        min_qty = _to_int(row.get("Min Order Qty")) or 1

        # Lead time
        lead_time_raw = _to_int(row.get("Lead-time in weeks"))

        # OEM Part Number(s) -> aliases
        oem_pns = _split_oem_pns(row.get("OEM Part Number"))

        # Application -> atributo
        application = _clean(row.get("Application"))

        # Pricing note si no hay precio
        pricing_note = "POA" if price is None else None

        # ── parts ──
        parts_rows.append({
            "part_number":      part_number_full,
            "part_number_root": part_number_root,
            "description":      description,
            "currency":         currency,
            "base_price":       price,
            "min_qty_default":  min_qty,
            "pricing_note":     pricing_note,
            "source_file":      source_file,
            "source_sheet":     "Sheet1",
        })

        # ── aliases: OEM Part Numbers ──
        for oem_pn in oem_pns:
            oem_full, _ = normalize_part_number(oem_pn)
            if oem_full and oem_full != part_number_full:
                alias_rows.append({
                    "part_number": part_number_full,
                    "alias_code":  oem_pn,
                    "source":      "OEM_PN",
                    "source_file": source_file,
                })

        # ── atributos ──
        extras: Dict[str, Any] = {
            "lead_time_weeks": str(lead_time_raw) if lead_time_raw is not None else None,
            "application":     application,
        }
        for k, v in extras.items():
            if v:
                attr_rows.append({
                    "part_number":  part_number_full,
                    "key":          k,
                    "value":        v,
                    "source_file":  source_file,
                    "source_sheet": "Sheet1",
                })

    parts_df   = pd.DataFrame(parts_rows)  if parts_rows  else pd.DataFrame()
    aliases_df = pd.DataFrame(alias_rows)  if alias_rows  else pd.DataFrame()
    attrs_df   = pd.DataFrame(attr_rows)   if attr_rows   else pd.DataFrame()

    return ParseResult(
        parts=parts_df,
        tiers=pd.DataFrame(),   # Keddeg no tiene tiers: 1 precio fijo por pieza
        aliases=aliases_df,
        attributes=attrs_df,
        parser_name=KeddegXlsxParser.name,
    )