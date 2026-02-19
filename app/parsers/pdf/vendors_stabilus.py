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



def parse_pdf_stabilus(pdf_bytes: bytes) -> pd.DataFrame:
    """
    Parser STABILUS (PDF por texto).
    Estructura típica:
        Art.-Nr.   ab Menge   VK
        082384     20         12,045 €
                   100        7,669 €
    Donde las filas "hijas" (sin Art.-Nr.) heredan el último Art.-Nr.
    """
    lines: List[str] = []
    for page_text in iter_text_pages_pypdf(pdf_bytes):
        for ln in (page_text or "").splitlines():
            s = ln.replace("\xa0", " ").strip()
            if s:
                lines.append(s)

    if not lines:
        return pd.DataFrame()

    current_pn: Optional[str] = None
    tiers: Dict[str, List[Tuple[int, float]]] = {}

    # patrones
    re_full = re.compile(
        r"^(?P<pn>[A-Z0-9\-]{4,})\s+(?P<qty>\d+(?:[\.,]\d+)?)\s+(?P<price>\d+(?:\.\d{3})*(?:,\d+)?)(?:\s*€)?$",
        re.IGNORECASE,
    )
    re_tier = re.compile(
        r"^(?P<qty>\d+(?:[\.,]\d+)?)\s+(?P<price>\d+(?:\.\d{3})*(?:,\d+)?)(?:\s*€)?$"
    )

    def _to_int_qty(s: str) -> Optional[int]:
        if not s:
            return None
        ss = str(s).strip()
        # STABILUS usa enteros, pero por si viene "100,0"
        ss = ss.replace(".", "").replace(",", ".")
        try:
            return int(float(ss))
        except Exception:
            m = re.search(r"\d+", ss)
            return int(m.group(0)) if m else None

    for ln in lines:
        low = ln.lower()
        if any(k in low for k in ("hansair", "sonderpreisliste", "gültig", "gueltig", "seite", "art.-nr", "ab menge", "vk")):
            continue

        ln2 = ln.replace("€", "").strip()

        m = re_full.match(ln2)
        if m:
            pn = m.group("pn").strip()
            qty = _to_int_qty(m.group("qty"))
            price = parse_price_value(m.group("price"))
            if qty is not None and price is not None:
                tiers.setdefault(pn, []).append((qty, price))
                current_pn = pn
            continue

        m2 = re_tier.match(ln2)
        if m2 and current_pn:
            qty = _to_int_qty(m2.group("qty"))
            price = parse_price_value(m2.group("price"))
            if qty is not None and price is not None:
                tiers.setdefault(current_pn, []).append((qty, price))
            continue

    if not tiers:
        return pd.DataFrame()

    # construir filas con max_qty (siguiente qty - 1)
    rows: List[Dict[str, Any]] = []
    for pn, arr in tiers.items():
        arr_sorted = sorted({q: p for q, p in arr}.items(), key=lambda t: t[0])  # dedupe por qty
        for i, (min_q, price) in enumerate(arr_sorted):
            next_min = arr_sorted[i + 1][0] if i + 1 < len(arr_sorted) else None
            max_q = (next_min - 1) if next_min is not None else None
            rows.append(
                {
                    "part_number": pn,
                    "min_qty": int(min_q),
                    "max_qty": int(max_q) if max_q is not None else None,
                    "price": float(price),
                    "currency": "EUR",
                }
            )

    return pd.DataFrame(rows)



class StabilusPdfParser:
    name = "pdf_stabilus"
    exts = (".pdf",)

    def sniff(self, ctx: ParseContext, head: bytes) -> int:
        f = (ctx.filename or '').lower()
        t = (ctx.first_text or '').lower()
        s = 0
        if ('stabilus' in f) or ('stabilus' in t) or ('stabilus' in (ctx.supplier_name or '').lower()): s += 70
        if ('preisliste' in f) or ('preisliste' in t) or ('preisliste' in (ctx.supplier_name or '').lower()): s += 30
        if ('sonderpreisliste' in f) or ('sonderpreisliste' in t) or ('sonderpreisliste' in (ctx.supplier_name or '').lower()): s += 30
        if ('art.-nr' in f) or ('art.-nr' in t) or ('art.-nr' in (ctx.supplier_name or '').lower()): s += 20
        return s

    def parse(self, file_bytes: bytes, ctx: ParseContext) -> ParseResult:
        raw = parse_pdf_stabilus(file_bytes)
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

register(StabilusPdfParser())
