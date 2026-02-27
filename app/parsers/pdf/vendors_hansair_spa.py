"""
Parser para cotizaciones SPA (Special Price Agreement) de Hansair / Parker.

Estos PDFs son generados por Parker Filtration para Hansair con precios especiales
por aerolínea cliente. El nombre del archivo SIEMPRE contiene "Hansair" y "SPA".

Ejemplos de nombres válidos:
  Hansair COPA SPA 19Nov2025.pdf
  Hansair EasyJet SPA 15Dec2025.pdf
  Hansair LATAM SPAs 23Dec2025.pdf
  Hansair United SPA 11Nov2025.pdf
  Hansair BA SPA 17Dec2025.pdf
  Hansair LH SPA 12Jan2026.pdf          ← nombre futuro, se detecta igual

Estructura del PDF (una sola página):
  - Cabeceras en líneas individuales (Customer, Part Number, etc.) — se ignoran
  - Líneas de datos con el formato:
      CUSTOMER CUSTOMER [REGION] PN DESCRIPTION BEG_DATE END_DATE [EST_USE]
      DIST_LIST_PRICE SUGG_CUST_PRICE SPA_PRICE [REBATE] [NOTES...]
  - Notas legales al final (se ignoran)

Columnas de precios (en orden):
  0: Dist List Price    <- precio de catálogo regular
  1: Suggested Customer Price  <- puede ser NA
  2: Distributor SPA Price     <- EL PRECIO QUE NOS INTERESA (puede ser NA)
  3: Rebate            <- se ignora

Formatos de precio observados:
  $123.45     ← formato estándar ($ delante)
  123.45$     ← formato BA ($ detrás)
  123.45 $    ← formato BA con espacio
  NA          ← sin precio acordado
  #VALUE!     ← error de Excel exportado, equivale a NA

Atributos generados por pieza:
  - spa_airline     <- aerolínea de la cotización (extraída del nombre del archivo o de los datos)
  - beg_date        <- fecha inicio vigencia
  - end_date        <- fecha fin vigencia
  - dist_list_price <- precio de lista original (para referencia)
  - pricing_note    <- "SPA/NA" si no hay SPA price
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import pandas as pd

from ..registry import register
from ..types import ParseContext, ParseResult
from ...transform.parse_utils import normalize_part_number
from .common import iter_text_pages_pypdf


# ── constantes ────────────────────────────────────────────────────────────────

# Regex para detectar el Part Number Parker (ej: 21FA312G, 38WS103G, 20D10-3, 21D10-2)
_PN_RE = re.compile(r"\b(\d{2}[A-Z]{1,4}\d+(?:-\d+)?[A-Z]?\b)")

# Fecha en formato M/D/YYYY
_DATE_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{4}")

# Líneas a ignorar (boilerplate)
_SKIP_PATTERNS = (
    "parker reserves the right",
    "parker agrees to issue rebates",
    "all rebate claims",
    "customer name",
    "region name",
    "part number",
    "beg date",
    "end date",
    "est use",
    "actual use",
    "on order",
    "dist list price",
    "suggested customer",
    "distributor spa",
    "rebate",
    "new end date",
    "afd cmments",
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _extract_airline_from_filename(filename: str) -> str:
    """Extrae nombre de aerolínea del nombre de archivo: 'Hansair COPA SPA ...' -> 'COPA'"""
    fn = filename or ""
    # Quitar extensión y normalizar
    fn = re.sub(r"\.pdf$", "", fn, flags=re.IGNORECASE).strip()
    # Formato: "Hansair <AIRLINE> SPA[s] <DATE>"
    m = re.match(r"Hansair\s+(.+?)\s+SPAs?\b", fn, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


def _extract_spa_price(rest: str) -> tuple[Optional[float], Optional[float], Optional[str], Optional[str]]:
    """
    Parsea el resto de la línea después de las dos fechas.
    Devuelve (dist_list_price, spa_price, beg_date, end_date).

    Estructura: [EST_USE?]  DIST_LIST  SUGG_CUST  SPA_PRICE  [REBATE] [NOTES]
    Índices:                    0           1          2
    """
    s = rest.strip()

    # Quitar Est Use (número entero puro al inicio, sin $)
    s = re.sub(r"^\d+\s+", "", s)

    # Extraer secuencia de valores (precio o NA) hasta texto de notas
    sequence: List[Optional[float]] = []
    pos = 0
    while pos < len(s):
        # Saltar espacios
        m_sp = re.match(r"\s+", s[pos:])
        if m_sp:
            pos += m_sp.end()
            continue
        # $ delante: $123.45
        m_p1 = re.match(r"\$([\d,]+\.?\d*)", s[pos:])
        if m_p1:
            try:
                sequence.append(float(m_p1.group(1).replace(",", "")))
            except ValueError:
                sequence.append(None)
            pos += m_p1.end()
            continue
        # $ detrás: 123.45$  o  123.45 $
        m_p2 = re.match(r"([\d,]+\.?\d*)\s*\$", s[pos:])
        if m_p2:
            try:
                sequence.append(float(m_p2.group(1).replace(",", "")))
            except ValueError:
                sequence.append(None)
            pos += m_p2.end()
            continue
        # NA o #VALUE!
        m_na = re.match(r"NA\b|#VALUE!", s[pos:], re.IGNORECASE)
        if m_na:
            sequence.append(None)
            pos += m_na.end()
            continue
        # Número decimal puro (rebate sin $, ej: 9.78 o 119.56)
        m_num = re.match(r"[\d,]+\.\d+", s[pos:])
        if m_num:
            try:
                sequence.append(float(m_num.group(0).replace(",", "")))
            except ValueError:
                sequence.append(None)
            pos += m_num.end()
            continue
        # Letra o carácter desconocido -> fin de precios
        break

    # Interpretar secuencia: [dist_list, sugg_cust, spa_price, ...]
    dist_list = sequence[0] if len(sequence) > 0 else None
    spa_price = sequence[2] if len(sequence) >= 3 else (sequence[1] if len(sequence) == 2 else None)

    return dist_list, spa_price


def _extract_dates(after_pn: str) -> tuple[Optional[str], Optional[str], str]:
    """Extrae las dos fechas y devuelve (beg_date, end_date, resto_sin_fechas)."""
    dates = list(_DATE_RE.finditer(after_pn))
    if len(dates) >= 2:
        beg = dates[0].group(0)
        end = dates[1].group(0)
        rest = after_pn[dates[1].end():].strip()
    elif len(dates) == 1:
        beg = dates[0].group(0)
        end = None
        rest = after_pn[dates[0].end():].strip()
    else:
        beg = end = None
        rest = after_pn
    return beg, end, rest


# ── parser class ──────────────────────────────────────────────────────────────

class HansairSPAPdfParser:
    """Parser para cotizaciones SPA Hansair/Parker (PDF)."""

    name = "pdf_hansair_spa"
    exts = (".pdf",)

    def sniff(self, ctx: ParseContext, head: bytes) -> int:
        score = 0
        fn = (ctx.filename or "").lower()
        txt = (ctx.first_text or "").lower()
        sup = (ctx.supplier_name or "").lower()

        # Señal principal: nombre contiene "hansair" + "spa"
        if "hansair" in fn and "spa" in fn:
            score += 70
        if "hansair" in sup and "spa" in sup:
            score += 70

        # Señales en el contenido del PDF
        if "distributor spa price" in txt:
            score += 20
        if "afd cmments" in txt or "suggested customer" in txt:
            score += 10

        return min(score, 100)

    def parse(self, file_bytes: bytes, ctx: ParseContext) -> ParseResult:
        return _parse_hansair_spa(file_bytes, ctx)


register(HansairSPAPdfParser())


# ── core parse function ───────────────────────────────────────────────────────

def _parse_hansair_spa(file_bytes: bytes, ctx: ParseContext) -> ParseResult:
    source_file = ctx.filename or ""
    airline = _extract_airline_from_filename(source_file)

    parts_rows: List[Dict[str, Any]] = []
    alias_rows: List[Dict[str, Any]] = []
    attr_rows:  List[Dict[str, Any]] = []

    for page_text in iter_text_pages_pypdf(file_bytes):
        if not page_text:
            continue

        for line in page_text.splitlines():
            raw = (line or "").strip()
            if not raw:
                continue
            low = raw.lower()

            # Ignorar cabeceras y notas legales
            if any(low.startswith(pat) or pat in low for pat in _SKIP_PATTERNS):
                continue

            # Buscar Part Number en la línea
            m_pn = _PN_RE.search(raw)
            if not m_pn:
                continue

            pn_str = m_pn.group(1)
            part_number_full, part_number_root = normalize_part_number(pn_str)

            # Descripción: texto entre PN y primera fecha
            after_pn = raw[m_pn.end():].strip()
            beg_date, end_date, rest_after_dates = _extract_dates(after_pn)

            # La descripción está ANTES de la primera fecha
            before_first_date = raw[m_pn.end():]
            m_first_date = _DATE_RE.search(before_first_date)
            if m_first_date:
                description = before_first_date[:m_first_date.start()].strip()
            else:
                description = after_pn[:50].strip()

            # Extraer precios del resto post-fechas
            dist_list, spa_price = _extract_spa_price(rest_after_dates)

            # Pricing note si no hay SPA price
            pricing_note = "SPA/NA" if spa_price is None else None

            # ── parts ──
            parts_rows.append({
                "part_number":      part_number_full,
                "part_number_root": part_number_root,
                "description":      description or None,
                "currency":         "USD",
                "base_price":       spa_price,
                "min_qty_default":  1,
                "pricing_note":     pricing_note,
                "source_file":      source_file,
                "source_sheet":     "SPA",
            })

            # ── atributos ──
            extras: Dict[str, Any] = {
                "spa_airline":      airline or None,
                "beg_date":         beg_date,
                "end_date":         end_date,
                "dist_list_price":  str(dist_list) if dist_list is not None else None,
            }
            for k, v in extras.items():
                if v:
                    attr_rows.append({
                        "part_number":  part_number_full,
                        "key":          k,
                        "value":        str(v),
                        "source_file":  source_file,
                        "source_sheet": "SPA",
                    })

    parts_df   = pd.DataFrame(parts_rows)  if parts_rows  else pd.DataFrame()
    aliases_df = pd.DataFrame(alias_rows)  if alias_rows  else pd.DataFrame()
    attrs_df   = pd.DataFrame(attr_rows)   if attr_rows   else pd.DataFrame()

    return ParseResult(
        parts=parts_df,
        tiers=pd.DataFrame(),
        aliases=aliases_df,
        attributes=attrs_df,
        parser_name=HansairSPAPdfParser.name,
    )