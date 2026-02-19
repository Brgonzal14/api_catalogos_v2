from __future__ import annotations

import re
import io
from typing import List, Dict, Any, Optional, Tuple
import pandas as pd

from ..registry import register
from ..types import ParseContext, ParseResult
from ...transform.result_builder import build_parse_result_from_df
from ...transform.parse_utils import parse_price_value
from .common import iter_text_pages_pypdf, first_page_text_pdfplumber, first_page_text_pypdf



def parse_pdf_hamilton(pdf_bytes: bytes) -> pd.DataFrame:
    """
    HAMILTON SUNDSTRAND 2026 (PDF):
      Cage code | Manufacturer Name | Part Number | Item Description | Net price | Unit of Measure | Currency | Lead Time | Standard Package Qty | Minimum Order Qty | Catalog Year

    Nota: en muchos casos pdfplumber detecta "tablas" de 1 columna (línea completa),
    por lo que aquí parseamos por texto (PyPDF2).
    """
    rows: List[Dict[str, Any]] = []

    pn_re = re.compile(r"^[A-Z0-9]+(?:[-/][A-Z0-9]+)*$")

    for page_text in iter_text_pages_pypdf(pdf_bytes):
        if not page_text:
            continue

        for line in page_text.splitlines():
            s = (line or "").strip()
            if not s:
                continue

            low = s.lower()
            # saltar títulos/headers
            if low.startswith("cage code") and "net price" in low:
                continue
            if "catalog for" in low and "hamilton sundstrand" in low:
                continue

            tokens = s.split()
            if len(tokens) < 11:
                continue

            year = tokens[-1]
            if not (year.isdigit() and len(year) == 4):
                continue

            # estructura estable desde el final
            moq = tokens[-2]
            spq = tokens[-3]
            lead_time = tokens[-4]
            currency = tokens[-5]
            uom = tokens[-6]
            price_raw = tokens[-7]

            # validar precio
            if parse_price_value(price_raw) is None:
                continue

            # bloque central (manufacturer + pn + description)
            price_idx = len(tokens) - 7
            mid = tokens[1:price_idx]
            if not mid:
                continue

            pn_idx = None
            for i, tok in enumerate(mid):
                if pn_re.match(tok) and any(ch.isdigit() for ch in tok):
                    pn_idx = i
                    break
            if pn_idx is None:
                continue

            manufacturer = " ".join(mid[:pn_idx]).strip() or None
            part_number = mid[pn_idx].strip()
            description = " ".join(mid[pn_idx + 1:]).strip() or None

            rows.append(
                {
                    "Cage code": tokens[0],
                    "Manufacturer Name": manufacturer,
                    "Part Number": part_number,
                    "Item Description": description,
                    "Net price": price_raw,
                    "Unit of Measure": uom,
                    "Currency": currency,
                    "Lead Time": lead_time,
                    "Standard Package Qty": spq,
                    "Minimum Order Qty": moq,
                    "Catalog Year": year,
                }
            )

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows)



class HamiltonPdfParser:
    name = "pdf_hamilton"
    exts = (".pdf",)

    def sniff(self, ctx: ParseContext, head: bytes) -> int:
        f = (ctx.filename or '').lower()
        t = (ctx.first_text or '').lower()
        s = 0
        if ('hamilton' in f) or ('hamilton' in t) or ('hamilton' in (ctx.supplier_name or '').lower()): s += 70
        if ('hamilton sundstrand' in f) or ('hamilton sundstrand' in t) or ('hamilton sundstrand' in (ctx.supplier_name or '').lower()): s += 30
        if ('cage code' in f) or ('cage code' in t) or ('cage code' in (ctx.supplier_name or '').lower()): s += 20
        if ('net price' in f) or ('net price' in t) or ('net price' in (ctx.supplier_name or '').lower()): s += 20
        return s

    def parse(self, file_bytes: bytes, ctx: ParseContext) -> ParseResult:
        raw = parse_pdf_hamilton(file_bytes)
        parts, tiers, aliases, attrs = build_parse_result_from_df(
            raw,
            source_file=ctx.filename,
            default_currency=_guess_currency(ctx.filename),
            parser_name=self.name,
        )
        return ParseResult(parts=parts, tiers=tiers, aliases=aliases, attributes=attrs, parser_name=self.name)

def _guess_currency(filename: str) -> str:
    f = (filename or "").lower()
    if "eur" in f or "€" in f:
        return "EUR"
    if "gbp" in f or "£" in f:
        return "GBP"
    return "USD"

register(HamiltonPdfParser())
