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



def parse_pdf_aes(pdf_bytes: bytes) -> pd.DataFrame:
    """AES Distributor price list (PDF con tablas)."""
    try:
        import pdfplumber
        tables_rows = []
        header = None
        for page_idx in range(0, 10):  # suficiente para captar la lista
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                if page_idx >= len(pdf.pages):
                    break
                page = pdf.pages[page_idx]
                tables = page.extract_tables() or []
                for table in tables:
                    for row in table:
                        # normaliza celdas
                        row = [c.strip() if isinstance(c, str) else c for c in row]
                        # detecta header real
                        if row and any(isinstance(c, str) and c.strip().lower() == "part number" for c in row):
                            header = row
                            continue
                        if header is None:
                            continue
                        # filas de datos: primera celda debe parecer P/N (no vacía)
                        if not row or row[0] is None:
                            continue
                        first = str(row[0]).strip()
                        if not first or first.lower().startswith("distributor"):
                            continue
                        tables_rows.append(row)
        if not header or not tables_rows:
            # fallback genérico
            return read_pdf_tables(pdf_bytes)

        # Ajustar longitudes
        max_len = max(len(r) for r in ([header] + tables_rows))
        def _pad(r): return r + [None] * (max_len - len(r))
        header = _pad(header)
        tables_rows = [_pad(r) for r in tables_rows]

        cols = []
        for c in header:
            c = (str(c).replace("\n", " ").strip() if c is not None else "")
            cols.append(c if c else f"col_{len(cols)}")

        df = pd.DataFrame(tables_rows, columns=cols)

        # estandarizar nombres clave (sin perder info: quedará como atributos también)
        rename = {}
        for c in df.columns:
            lc = str(c).lower()
            if lc == "part number":
                rename[c] = "part_number"
            elif "description" in lc:
                rename[c] = "description"
            elif "price" in lc:
                rename[c] = "price"
            elif lc.strip() == "moq":
                rename[c] = "min_qty"
            elif "certificate" in lc:
                rename[c] = "certificate"
            elif "length in inch" in lc:
                rename[c] = "length_inch"
            elif "length in cm" in lc:
                rename[c] = "length_cm"
        return df.rename(columns=rename)

    except Exception:
        # fallback genérico
        return read_pdf_tables(pdf_bytes)



class AESPdfParser:
    name = "pdf_aes"
    exts = (".pdf",)

    def sniff(self, ctx: ParseContext, head: bytes) -> int:
        f = (ctx.filename or '').lower()
        t = (ctx.first_text or '').lower()
        s = 0
        if ('aes' in f) or ('aes' in t) or ('aes' in (ctx.supplier_name or '').lower()): s += 40
        if ('distributor' in f) or ('distributor' in t) or ('distributor' in (ctx.supplier_name or '').lower()): s += 30
        if ('price list' in f) or ('price list' in t) or ('price list' in (ctx.supplier_name or '').lower()): s += 20
        if ('part number' in f) or ('part number' in t) or ('part number' in (ctx.supplier_name or '').lower()): s += 20
        return s

    def parse(self, file_bytes: bytes, ctx: ParseContext) -> ParseResult:
        raw = parse_pdf_aes(file_bytes)
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

register(AESPdfParser())
