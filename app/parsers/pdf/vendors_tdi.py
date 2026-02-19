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



def parse_pdf_tdi(pdf_bytes: bytes) -> pd.DataFrame:
    """
    TDI Meridian / Spectrum (PDF):
    Extrae desde texto con pdfplumber:
      Part Number | Part Description | Seat Model | Aircraft | Leadtime | Unit | Sales Price | Min Buy
    """
    try:
        import pdfplumber
    except ImportError as e:
        raise ImportError(f"Falta pdfplumber para leer PDF TDI: {e}")

    rows: List[Dict[str, Any]] = []
    pn_re = re.compile(r"^[A-Z0-9]+(?:[-/][A-Z0-9]+)*$")

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            if not page_text:
                continue
            for line in page_text.splitlines():
                s = (line or "").strip()
                if not s:
                    continue

                low = s.lower()
                # saltar títulos/cabeceras/notes
                if low.startswith("torrington") or "pricing" in low and "part number" in low:
                    continue
                if low.startswith("page ") or low.startswith("tdi standard"):
                    continue
                if low.startswith("part number") or low.startswith("part description"):
                    continue

                tokens = s.split()
                if len(tokens) < 6:
                    continue

                pn = tokens[0].strip()
                if not pn_re.match(pn):
                    continue

                # desde el final: min buy, price, unit
                min_buy = tokens[-1] if tokens[-1].isdigit() else None
                price_raw = tokens[-2] if len(tokens) >= 2 else None
                unit = tokens[-3] if len(tokens) >= 3 else None

                if min_buy is None:
                    continue
                if parse_price_value(price_raw) is None:
                    continue

                # leadtime: buscamos 'days' o 'weeks'
                lead_idx = None
                for i in range(len(tokens) - 4, 0, -1):
                    if tokens[i].lower() in ("days", "day", "weeks", "week"):
                        lead_idx = i
                        break

                leadtime = None
                seat_model = None
                aircraft = None

                if lead_idx is not None:
                    # ejemplo: "100 Business Days"
                    lead_start = max(1, lead_idx - 2)
                    leadtime = " ".join(tokens[lead_start:lead_idx + 1]).strip()

                    pre = tokens[:lead_start]  # pn + desc + seat/aircraft...
                    if len(pre) >= 3:
                        seat_model = pre[-2]
                        aircraft = pre[-1]
                        desc_tokens = pre[1:-2]
                    elif len(pre) == 2:
                        desc_tokens = pre[1:]
                    else:
                        desc_tokens = []
                else:
                    # fallback: asumimos que el texto entre PN y los 3 últimos tokens es descripción
                    desc_tokens = tokens[1:-3]

                description = " ".join(desc_tokens).strip()

                rows.append({
                    "Part Number": pn,
                    "Part Description": description,
                    "Seat Model": seat_model,
                    "Aircraft": aircraft,
                    "Leadtime": leadtime,
                    "Unit": unit,
                    "Sales Price": price_raw,
                    "Min Buy": min_buy,
                })

    return pd.DataFrame(rows)


# ============================================================
#  Helpers para precios por tramos
# ============================================================


class TDIPdfParser:
    name = "pdf_tdi"
    exts = (".pdf",)

    def sniff(self, ctx: ParseContext, head: bytes) -> int:
        f = (ctx.filename or '').lower()
        t = (ctx.first_text or '').lower()
        s = 0
        if ('tdi' in f) or ('tdi' in t) or ('tdi' in (ctx.supplier_name or '').lower()): s += 60
        if ('meridian' in f) or ('meridian' in t) or ('meridian' in (ctx.supplier_name or '').lower()): s += 20
        if ('spectrum' in f) or ('spectrum' in t) or ('spectrum' in (ctx.supplier_name or '').lower()): s += 20
        if ('pricing' in f) or ('pricing' in t) or ('pricing' in (ctx.supplier_name or '').lower()): s += 20
        if ('torrington' in f) or ('torrington' in t) or ('torrington' in (ctx.supplier_name or '').lower()): s += 20
        return s

    def parse(self, file_bytes: bytes, ctx: ParseContext) -> ParseResult:
        raw = parse_pdf_tdi(file_bytes)
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

register(TDIPdfParser())
