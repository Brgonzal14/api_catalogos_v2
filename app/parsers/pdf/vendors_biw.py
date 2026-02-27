"""
Parser para catálogos BIW Isolierstoffe GmbH (PDF).

Estructura del archivo (una sola página):
  - Cabecera: "BIW-Teilenummer BIW-Teilebeschreibung Hansair / Airbus-Teilenummer
               Zeichnungsnummer Staffelmenge Staffelpreis Preiseinheit"
  - Datos: una fila por tramo de precio (múltiples filas por BIW-PN = tiers)
  - Formato de línea:
      60088653  SI-SCH. PROFIL,(3/10)SELBSTK. 2643/ 52  ABS5784AB3-3  ABS5784AB3-3  100  555,64 €  100m
      ^^^^^^^^  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^  ^^^^^^^^^^^^  ^^^^^^^^^^^^  ^^^  ^^^^^^^^   ^^^^
      BIW_PN    descripción                               hansair_pn    drawing_pn    qty  precio    uom

Campos extraídos:
  - part_number     <- BIW-Teilenummer (8 dígitos)
  - description     <- BIW-Teilebeschreibung
  - hansair_pn      <- Hansair/Airbus-Teilenummer (alias)
  - tiers           <- una entrada por Staffelmenge/Staffelpreis
  - currency        <- EUR (siempre, € explícito en la línea)
  - uom             <- Preiseinheit (100m, m, etc.)

Notas:
  - Precios en formato alemán: "1.007,40" -> 1007.40 (punto=sep miles, coma=decimal)
  - Staffelmenge también con punto como sep de miles: "14.400" -> 14400
  - El Hansair PN y Zeichnungsnummer suelen ser idénticos; se usa como alias una sola vez por PN
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import pandas as pd

from ..registry import register
from ..types import ParseContext, ParseResult
from ...transform.parse_utils import normalize_part_number
from .common import iter_text_pages_pypdf


# ── helpers ────────────────────────────────────────────────────────────────────

def _parse_de_price(s: str) -> Optional[float]:
    """Parsea precio en formato alemán: '1.007,40' -> 1007.40, '555,64' -> 555.64"""
    if not s:
        return None
    # Quitar puntos de sep miles, reemplazar coma decimal por punto
    cleaned = s.replace(".", "").replace(",", ".")
    try:
        v = float(cleaned)
        return v if v >= 0 else None
    except ValueError:
        return None


def _parse_de_qty(s: str) -> Optional[int]:
    """Parsea cantidad en formato alemán: '14.400' -> 14400, '100' -> 100"""
    if not s:
        return None
    cleaned = s.replace(".", "")
    try:
        return int(cleaned)
    except ValueError:
        return None


# ── regex de línea de datos ────────────────────────────────────────────────────
# Formato: 8 dígitos | descripción libre | hansair_pn | drawing_pn | qty | precio € | uom
_ROW_RE = re.compile(
    r"^(?P<biw_pn>\d{8})\s+"
    r"(?P<desc>.+?)\s+"
    r"(?P<hansair>[A-Z0-9][A-Z0-9\-]+)\s+"   # Hansair/Airbus PN
    r"(?P<drawing>[A-Z0-9][A-Z0-9\-]+)\s+"   # Zeichnungsnummer
    r"(?P<qty>[\d.]+)\s+"                     # Staffelmenge
    r"(?P<price>[\d.,]+)\s+€\s+"              # Staffelpreis
    r"(?P<uom>\S+)\s*$"                       # Preiseinheit
)

_SKIP_STARTS = (
    "silicone rubber", "biw isolierstoffe", "preisliste", "kundennummer",
    "hansair logistics", "gutenbergring", "norderstedt", "yakob",
    "bitte folgende", "unsere preisliste", "des weiteren", "des vertrags",
    "bestimmtheit", "ebenfalls", "rohstoffe",
    "biw-teilenummer",  # cabecera de tabla
    "05.11.2025",
)


# ── parser class ───────────────────────────────────────────────────────────────

class BIWPdfParser:
    """Parser para listas de precios BIW Isolierstoffe GmbH (PDF)."""

    name = "pdf_biw"
    exts = (".pdf",)

    _SIGNALS = ("biw", "isolierstoffe", "biw_preisliste", "biw preisliste")

    def sniff(self, ctx: ParseContext, head: bytes) -> int:
        score = 0
        fn = (ctx.filename or "").lower()
        sup = (ctx.supplier_name or "").lower()
        txt = (ctx.first_text or "").lower()

        for kw in self._SIGNALS:
            if kw in fn:
                score += 40
            if kw in sup:
                score += 40

        if "biw isolierstoffe" in txt or "biw-teilenummer" in txt:
            score += 40
        if "staffelpreis" in txt or "staffelmenge" in txt:
            score += 20

        return min(score, 100)

    def parse(self, file_bytes: bytes, ctx: ParseContext) -> ParseResult:
        return _parse_biw(file_bytes, ctx)


register(BIWPdfParser())


# ── core parse function ────────────────────────────────────────────────────────

def _parse_biw(file_bytes: bytes, ctx: ParseContext) -> ParseResult:
    source_file = ctx.filename or ""

    # Recopilar todas las filas crudas (una por tramo de precio)
    raw_rows: List[Dict[str, Any]] = []

    for page_text in iter_text_pages_pypdf(file_bytes):
        if not page_text:
            continue
        for line in page_text.splitlines():
            raw = (line or "").strip()
            if not raw:
                continue
            low = raw.lower()

            # Ignorar boilerplate
            if any(low.startswith(s) for s in _SKIP_STARTS):
                continue

            m = _ROW_RE.match(raw)
            if not m:
                continue

            biw_pn   = m.group("biw_pn").strip()
            desc     = m.group("desc").strip()
            hansair  = m.group("hansair").strip()
            qty      = _parse_de_qty(m.group("qty"))
            price    = _parse_de_price(m.group("price"))
            uom      = m.group("uom").strip()

            if qty is None or price is None:
                continue

            raw_rows.append({
                "biw_pn":   biw_pn,
                "desc":     desc,
                "hansair":  hansair,
                "qty":      qty,
                "price":    price,
                "uom":      uom,
            })

    if not raw_rows:
        return ParseResult(
            parts=pd.DataFrame(), tiers=pd.DataFrame(),
            aliases=pd.DataFrame(), attributes=pd.DataFrame(),
            parser_name=BIWPdfParser.name,
        )

    # Agrupar por BIW_PN para construir tiers y elegir precio base
    # Ordenar por qty ascendente dentro de cada PN
    from collections import defaultdict
    by_pn: Dict[str, List[Dict]] = defaultdict(list)
    for r in raw_rows:
        by_pn[r["biw_pn"]].append(r)

    parts_rows:  List[Dict[str, Any]] = []
    tiers_rows:  List[Dict[str, Any]] = []
    alias_rows:  List[Dict[str, Any]] = []
    attr_rows:   List[Dict[str, Any]] = []

    for biw_pn, rows in by_pn.items():
        part_number_full, part_number_root = normalize_part_number(biw_pn)

        # Ordenar tramos por cantidad ascendente y deduplicar (mismo qty+price)
        rows_sorted = sorted(rows, key=lambda r: (r["qty"], r["price"]))
        seen_tiers = set()
        unique_rows = []
        for r in rows_sorted:
            key = (r["qty"], r["price"])
            if key not in seen_tiers:
                seen_tiers.add(key)
                unique_rows.append(r)

        # Datos comunes del PN (del primer tramo)
        first = unique_rows[0]
        description = first["desc"]
        hansair_pn  = first["hansair"]
        uom         = first["uom"]
        currency    = "EUR"

        # Precio base = precio del tramo de menor qty
        base_price = unique_rows[0]["price"]
        min_qty    = unique_rows[0]["qty"]

        # ── parts ──
        parts_rows.append({
            "part_number":      part_number_full,
            "part_number_root": part_number_root,
            "description":      description,
            "currency":         currency,
            "base_price":       base_price,
            "min_qty_default":  min_qty,
            "source_file":      source_file,
            "source_sheet":     "BIW_Preisliste",
        })

        # ── tiers (un tramo por qty única, con max_qty calculado) ──
        for i, r in enumerate(unique_rows):
            if i + 1 < len(unique_rows):
                max_qty = unique_rows[i + 1]["qty"] - 1
            else:
                max_qty = None
            tiers_rows.append({
                "part_number": part_number_full,
                "min_qty":     r["qty"],
                "max_qty":     max_qty,
                "unit_price":  r["price"],
                "currency":    currency,
                "source_file": source_file,
                "source_sheet": "BIW_Preisliste",
            })

        # ── alias: Hansair/Airbus PN ──
        hansair_full, _ = normalize_part_number(hansair_pn)
        if hansair_full and hansair_full != part_number_full:
            alias_rows.append({
                "part_number": part_number_full,
                "alias_code":  hansair_pn,
                "source":      "HANSAIR_PN",
                "source_file": source_file,
            })

        # ── atributos ──
        for k, v in [("uom", uom), ("hansair_pn", hansair_pn)]:
            if v:
                attr_rows.append({
                    "part_number":  part_number_full,
                    "key":          k,
                    "value":        str(v),
                    "source_file":  source_file,
                    "source_sheet": "BIW_Preisliste",
                })

    parts_df   = pd.DataFrame(parts_rows)
    tiers_df   = pd.DataFrame(tiers_rows)
    aliases_df = pd.DataFrame(alias_rows)
    attrs_df   = pd.DataFrame(attr_rows)

    return ParseResult(
        parts=parts_df,
        tiers=tiers_df,
        aliases=aliases_df,
        attributes=attrs_df,
        parser_name=BIWPdfParser.name,
    )