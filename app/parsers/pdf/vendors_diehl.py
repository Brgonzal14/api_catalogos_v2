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



def parse_pdf_diehl(pdf_bytes: bytes) -> pd.DataFrame:
    """
    DIEHL Broker Price Catalogue (PDF):
      PNR | Description | Effect Date (YYYYMMDD) | UNT | Price 2026 | CUR | MOQ | SPQ | LTM
    Nota: el formato viene "en línea", por eso se extrae desde texto (pypdf).
    """
    rows: List[Dict[str, Any]] = []
    date_re = re.compile(r"^\d{8}$")

    for page_text in iter_text_pages_pypdf(pdf_bytes):
        if not page_text:
            continue

        for line in page_text.splitlines():
            s = (line or "").strip()
            if not s:
                continue

            low = s.lower()
            # saltar cabeceras
            if low.startswith("pnr ") or low.startswith("pnr\t"):
                continue
            if "broker net price list" in low or "diehl aviation" in low:
                # puede ser cabecera de página
                continue

            tokens = s.split()
            if len(tokens) < 6:
                continue

            pnr = tokens[0]

            # encontrar índice de fecha efecto
            date_idx = None
            for i, tok in enumerate(tokens):
                if date_re.match(tok):
                    date_idx = i
                    break
            if date_idx is None or date_idx < 2:
                continue
            if date_idx + 3 >= len(tokens):
                continue

            desc = " ".join(tokens[1:date_idx]).strip()
            eff_date = tokens[date_idx]
            unt = tokens[date_idx + 1]
            price_raw = tokens[date_idx + 2]
            cur = tokens[date_idx + 3]

            # resto: MOQ [SPQ LTM] o "on request"
            rest = tokens[date_idx + 4:]
            moq = rest[0] if len(rest) >= 1 else None
            spq = None
            ltm = None

            if len(rest) >= 3 and rest[1].isdigit() and rest[2].isdigit():
                spq = rest[1]
                ltm = rest[2]
            else:
                # Ej: "1 on request"
                tail = " ".join(rest[1:]).strip() if len(rest) > 1 else ""
                if tail:
                    ltm = tail

            # validar precio
            if parse_price_value(price_raw) is None:
                continue

            rows.append(
                {
                    "PNR": pnr,
                    "Description": desc,
                    "Effect Date": eff_date,
                    "UNT": unt,
                    "Price 2026": price_raw,
                    "CUR": cur,
                    "MOQ": moq,
                    "SPQ": spq,
                    "LTM": ltm,
                }
            )

    return pd.DataFrame(rows)



# ============================================================
#  Helpers PDF (vendor-specific): AES / MATZEN & TIMM / STABILUS / STUKERJURGEN SAC / VINCORION
#  Nota: algunos de estos PDFs no exponen tablas a pdfplumber, por lo que se parsean por texto.
# ============================================================



class DiehlPdfParser:
    name = "pdf_diehl"
    exts = (".pdf",)

    def sniff(self, ctx: ParseContext, head: bytes) -> int:
        f = (ctx.filename or '').lower()
        t = (ctx.first_text or '').lower()
        s = 0
        if ('diehl' in f) or ('diehl' in t) or ('diehl' in (ctx.supplier_name or '').lower()): s += 80
        if ('broker net price list' in f) or ('broker net price list' in t) or ('broker net price list' in (ctx.supplier_name or '').lower()): s += 30
        return s

    def parse(self, file_bytes: bytes, ctx: ParseContext) -> ParseResult:
        raw = parse_pdf_diehl(file_bytes)
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

register(DiehlPdfParser())
