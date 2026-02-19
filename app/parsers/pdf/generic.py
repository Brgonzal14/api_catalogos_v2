from __future__ import annotations
import pandas as pd
from ..types import ParseContext, ParseResult
from .common import read_pdf_tables
from ..registry import register
from ...transform.result_builder import build_parse_result_from_df

class GenericPdfTableParser:
    name = "pdf_table_generic"
    exts = (".pdf",)

    def sniff(self, ctx: ParseContext, head: bytes) -> int:
        # fallback: bajo score
        return 10

    def parse(self, file_bytes: bytes, ctx: ParseContext) -> ParseResult:
        df = read_pdf_tables(file_bytes)
        parts, tiers, aliases, attrs = build_parse_result_from_df(
            df,
            source_file=ctx.filename,
            default_currency=_guess_currency(ctx.filename),
            parser_name=self.name,
        )
        return ParseResult(parts=parts, tiers=tiers, aliases=aliases, attributes=attrs, parser_name=self.name)

def _guess_currency(filename: str) -> str:
    f=(filename or "").lower()
    if "eur" in f or "€" in f:
        return "EUR"
    if "gbp" in f or "£" in f:
        return "GBP"
    return "USD"
