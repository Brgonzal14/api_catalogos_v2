from __future__ import annotations

import pandas as pd
from ..registry import register
from ..types import ParseContext, ParseResult
from .generic import parse_xlsx_generic

class GMIXlsxParser:
    name = "xlsx_gmi"
    exts = (".xlsx", ".xls")

    def sniff(self, ctx: ParseContext, head: bytes) -> int:
        f = (ctx.filename or "").lower()
        s = (ctx.supplier_name or "").lower()
        t = (ctx.first_text or "").lower()
        score = 0
        if "gmi" in f or "gmi" in s:
            score += 80
        # el archivo típico contiene muchas hojas con equipos (ANITA, Blankets, etc.)
        if any(k in f for k in ("catalog", "pricelist", "price")):
            score += 10
        return score

    def parse(self, file_bytes: bytes, ctx: ParseContext) -> ParseResult:
        res = parse_xlsx_generic(file_bytes, ctx)
        # GMI suele ser EUR
        if not res.parts.empty:
            res.parts.loc[res.parts["currency"].isna() | (res.parts["currency"].astype(str).str.strip() == ""), "currency"] = "EUR"
        if not res.tiers.empty:
            res.tiers.loc[res.tiers["currency"].isna() | (res.tiers["currency"].astype(str).str.strip() == ""), "currency"] = "EUR"

        # eliminar filas obviamente no-PN
        if "part_number" in res.parts.columns and not res.parts.empty:
            bad = res.parts["part_number"].astype(str).str.lower().str.contains(r"^(version|date|read|me)$")
            res.parts = res.parts.loc[~bad].reset_index(drop=True)

        res.parser_name = self.name
        return res

register(GMIXlsxParser())
