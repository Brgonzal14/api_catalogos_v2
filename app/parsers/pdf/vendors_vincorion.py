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




_albany_token = r"(?:-|[A-Z0-9][A-Z0-9\-/\.]*\d[A-Z0-9\-/\.]*)"
_albany_row_re = re.compile(
    rf"^\s*(?P<boeing>{_albany_token})\s+(?P<current>{_albany_token})\s+(?P<previous>{_albany_token})\s+"
    r"(?P<desc>.+?)\s+(?P<qty>\d+(?:\.\d+)?)\s+(?P<uom>[A-Za-z\.]+)\s+\$(?P<price>[\d,]+\.\d{2})\s*$"
)
_albany_price_pat = re.compile(r"\$\s*[\d,]+\.\d{2}\b")
_holcim_price_re = re.compile(r"€\s*(\d[\d\.]*,\d{2}|\d[\d,]*\.\d{2})")


def parse_pdf_vincorion(pdf_bytes: bytes) -> pd.DataFrame:
    """VINCORION Airbus spares: parseo por texto (ATA | Partnumber | Designation | Lead Time | Price US-$)."""
    rows = []
    try:
        header_seen = False
        for page_text in iter_text_pages_pypdf(pdf_bytes):
            if not page_text:
                continue
            for line in page_text.splitlines():
                raw = (line or "").strip()
                if not raw:
                    continue
                low = raw.lower()

                if "ata partnumber designation of item lead time price" in low:
                    header_seen = True
                    continue
                if not header_seen:
                    continue

                # cortar cuando viene otro bloque
                if low.startswith("annual price catalogue"):
                    continue
                if "replacements" in low and "ata" not in low:
                    continue
                if low.startswith("control units") or low.startswith("hydraulic") or low.startswith("electrical"):
                    # títulos de sección
                    continue

                # líneas válidas empiezan con ATA (2 dígitos)
                m = re.match(r"^(?P<ata>\d{2})\s+(?P<pn>[A-Za-z0-9\-\/]+)\s+(?P<rest>.+)$", raw)
                if not m:
                    continue
                ata = m.group("ata")
                pn = m.group("pn").strip()
                rest = m.group("rest").strip()

                # precio: último token numérico
                toks = rest.split()
                if not toks:
                    continue
                price_token = toks[-1]
                price = parse_price_value(price_token)
                if price is None:
                    continue
                rest_wo_price = " ".join(toks[:-1]).strip()

                lead_time = None
                # buscar "on request"
                if re.search(r"\bon\s+request\b", rest_wo_price, flags=re.IGNORECASE):
                    lead_time = "on request"
                    desc = re.sub(r"\bon\s+request\b", "", rest_wo_price, flags=re.IGNORECASE).strip()
                else:
                    mlt = re.search(r"(\d+\s*(?:day|days|week|weeks|month|months))", rest_wo_price, flags=re.IGNORECASE)
                    if mlt:
                        lead_time = mlt.group(1)
                        desc = rest_wo_price[:mlt.start()].strip()
                    else:
                        desc = rest_wo_price

                rows.append({
                    "ata": ata,
                    "part_number": pn,
                    "description": desc,
                    "lead_time": lead_time,
                    "price": price,
                    "currency": "USD",
                })

        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame(rows)






# ============================================================
#  NUEVOS PDF (ALBANY / HOLCIM)
# ============================================================

_albany_token = r"(?:-|[A-Z0-9][A-Z0-9\-/\.]*\d[A-Z0-9\-/\.]*)"
_albany_row_re = re.compile(
    rf"^\s*(?P<boeing>{_albany_token})\s+(?P<current>{_albany_token})\s+(?P<previous>{_albany_token})\s+"
    r"(?P<desc>.+?)\s+(?P<qty>\d+(?:\.\d+)?)\s+(?P<uom>[A-Za-z\.]+)\s+\$(?P<price>[\d,]+\.\d{2})\s*$"
)
_albany_price_pat = re.compile(r"\$\s*[\d,]+\.\d{2}\b")



class VincorionPdfParser:
    name = "pdf_vincorion"
    exts = (".pdf",)

    def sniff(self, ctx: ParseContext, head: bytes) -> int:
        f = (ctx.filename or '').lower()
        t = (ctx.first_text or '').lower()
        s = 0
        if ('vincorion' in f) or ('vincorion' in t) or ('vincorion' in (ctx.supplier_name or '').lower()): s += 80
        if ('annual price catalogue' in f) or ('annual price catalogue' in t) or ('annual price catalogue' in (ctx.supplier_name or '').lower()): s += 20
        if ('spare parts' in f) or ('spare parts' in t) or ('spare parts' in (ctx.supplier_name or '').lower()): s += 10
        return s

    def parse(self, file_bytes: bytes, ctx: ParseContext) -> ParseResult:
        raw = parse_pdf_vincorion(file_bytes)
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

register(VincorionPdfParser())
