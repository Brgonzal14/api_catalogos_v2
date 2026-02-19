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



def parse_pdf_ipeco(pdf_bytes: bytes) -> pd.DataFrame:
    """
    IPECO 2026 Dollar Price List (PDF):
      Material | Description | Price (USD) | Per | UoM | Of | Lead Time (days) | Export Licence
    Nota: se extrae desde texto (pypdf), no desde tablas.
    """
    rows: List[Dict[str, Any]] = []

    for page_text in iter_text_pages_pypdf(pdf_bytes):
        if not page_text:
            continue

        for line in page_text.splitlines():
            s = (line or "").strip()
            if not s:
                continue

            low = s.lower()
            # saltar cabeceras y títulos
            if low.startswith("material") and ("price" in low or "uom" in low):
                continue
            if "ipeco" in low and "price list" in low:
                continue

            tokens = s.split()
            if len(tokens) < 7:
                continue

            part_no = tokens[0]

            # Encuentra los 2 últimos tokens NUMÉRICOS: Of y LeadTime
            num_idxs = [i for i, tok in enumerate(tokens) if tok.isdigit()]
            if len(num_idxs) < 2:
                continue

            lead_idx = num_idxs[-1]
            of_idx = num_idxs[-2]

            # Estructura esperada al final:
            # ... <price> <per> <uom> <of> <lead> [export_licence...]
            uom_idx = of_idx - 1
            per_idx = uom_idx - 1
            price_idx = per_idx - 1

            if price_idx < 2 or lead_idx <= of_idx:
                continue

            price_raw = tokens[price_idx]
            price_val = parse_price_value(price_raw)
            if price_val is None:
                continue

            description = " ".join(tokens[1:price_idx]).strip()
            per_qty = tokens[per_idx]
            uom = tokens[uom_idx]
            of_qty = tokens[of_idx]
            lead_time = tokens[lead_idx]
            export_lic = " ".join(tokens[lead_idx + 1:]).strip() or None

            rows.append(
                {
                    "Material": part_no,
                    "Description": description,
                    "Price (USD)": price_raw,
                    "Currency": "USD",
                    "Per": per_qty,
                    "UoM": uom,
                    "Of": of_qty,
                    "Lead Time (days)": lead_time,
                    "Export Licence": export_lic,
                }
            )

    return pd.DataFrame(rows)




class IpecoPdfParser:
    name = "pdf_ipeco"
    exts = (".pdf",)

    def sniff(self, ctx: ParseContext, head: bytes) -> int:
        f = (ctx.filename or '').lower()
        t = (ctx.first_text or '').lower()
        s = 0
        if ('ipeco' in f) or ('ipeco' in t) or ('ipeco' in (ctx.supplier_name or '').lower()): s += 80
        if ('dollar price list' in f) or ('dollar price list' in t) or ('dollar price list' in (ctx.supplier_name or '').lower()): s += 20
        if ('material' in f) or ('material' in t) or ('material' in (ctx.supplier_name or '').lower()): s += 10
        return s

    def parse(self, file_bytes: bytes, ctx: ParseContext) -> ParseResult:
        raw = parse_pdf_ipeco(file_bytes)
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

register(IpecoPdfParser())
