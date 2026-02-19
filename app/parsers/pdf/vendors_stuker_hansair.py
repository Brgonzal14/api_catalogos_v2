from __future__ import annotations

import pandas as pd
from ..registry import register
from ..types import ParseContext, ParseResult
from .common import read_pdf_tables
from ...transform.result_builder import build_parse_result_from_df

class StukerHansairPdfParser:
    name = "pdf_stuker_hansair"
    exts = (".pdf",)

    def sniff(self, ctx: ParseContext, head: bytes) -> int:
        f = (ctx.filename or "").lower()
        t = (ctx.first_text or "").lower()
        s = 0
        if "stuker" in f or "stüker" in f or "stuker" in (ctx.supplier_name or "").lower():
            s += 40
        if "hansair" in f or "hansair" in t:
            s += 60
        # si no es SAC, suele ser el Hansair
        if "sac products" not in t and "bauteil-nr" not in t:
            s += 10
        return s

    def parse(self, file_bytes: bytes, ctx: ParseContext) -> ParseResult:
        raw = read_pdf_tables(file_bytes)
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
    return "USD"

register(StukerHansairPdfParser())
