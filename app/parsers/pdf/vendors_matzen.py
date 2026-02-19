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



def parse_pdf_matzen(pdf_bytes: bytes) -> pd.DataFrame:
    """MATZEN & TIMM (AIRBUS): Airbus P/N principal y Supplier P/N como alias."""
    try:
        import pdfplumber
        rows = []
        header = None
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables() or []
                for table in tables:
                    for row in table:
                        row = [c.strip() if isinstance(c, str) else c for c in row]
                        # header contiene "Customer P/N" y "Description"
                        if row and any(isinstance(c, str) and "customer p/n" in c.lower() for c in row) and any(isinstance(c, str) and "description" in c.lower() for c in row):
                            header = row
                            continue
                        if header is None:
                            continue
                        if not row or row[0] is None:
                            continue
                        # ignora paginación / títulos
                        if isinstance(row[0], str) and row[0].lower().startswith("spare parts"):
                            continue
                        rows.append(row)

        if not header or not rows:
            return read_pdf_tables(pdf_bytes)

        max_len = max(len(r) for r in ([header] + rows))
        def _pad(r): return r + [None] * (max_len - len(r))
        header = _pad(header)
        rows = [_pad(r) for r in rows]

        cols = []
        for c in header:
            c = (str(c).replace("\n", " ").strip() if c is not None else "")
            cols.append(c if c else f"col_{len(cols)}")

        df = pd.DataFrame(rows, columns=cols)

        # Renames: Airbus PN principal
        rename = {}
        for c in df.columns:
            lc = str(c).lower()
            if "customer p/n" in lc:
                rename[c] = "part_number"  # AIRBUS P/N
            elif "m&t p/n" in lc or "m&t" in lc and "p/n" in lc:
                rename[c] = "supplier_part_no"  # alias
            elif lc.strip() == "customer":
                rename[c] = "customer"
            elif "description" in lc:
                rename[c] = "description"
            elif "ata" in lc:
                rename[c] = "ata_chapter"
            elif "shelf" in lc:
                rename[c] = "shelf_life"
            elif "price" in lc:
                rename[c] = "price"
            elif "pack" in lc and "size" in lc:
                rename[c] = "pack_qty"
            elif "qty" in lc and "pack" in lc:
                rename[c] = "pack_qty"
        return df.rename(columns=rename)

    except Exception:
        return read_pdf_tables(pdf_bytes)



class MatzenPdfParser:
    name = "pdf_matzen"
    exts = (".pdf",)

    def sniff(self, ctx: ParseContext, head: bytes) -> int:
        f = (ctx.filename or '').lower()
        t = (ctx.first_text or '').lower()
        s = 0
        if ('matzen' in f) or ('matzen' in t) or ('matzen' in (ctx.supplier_name or '').lower()): s += 70
        if ('timm' in f) or ('timm' in t) or ('timm' in (ctx.supplier_name or '').lower()): s += 30
        return s

    def parse(self, file_bytes: bytes, ctx: ParseContext) -> ParseResult:
        raw = parse_pdf_matzen(file_bytes)
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

register(MatzenPdfParser())
