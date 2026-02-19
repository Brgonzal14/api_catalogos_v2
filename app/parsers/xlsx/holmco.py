from __future__ import annotations

import pandas as pd
from ..registry import register
from ..types import ParseContext, ParseResult
from .generic import parse_xlsx_generic
from .common import extract_alias_codes
from ...transform.parse_utils import normalize_pn

class HolmcoXlsxParser:
    name = "xlsx_holmco"
    exts = (".xlsx", ".xls")

    def sniff(self, ctx: ParseContext, head: bytes) -> int:
        f = (ctx.filename or "").lower()
        s = (ctx.supplier_name or "").lower()
        score = 0
        if "holmco" in f or "holmco" in s:
            score += 90
        if "price list" in f or "pricelist" in f:
            score += 10
        return score

    def parse(self, file_bytes: bytes, ctx: ParseContext) -> ParseResult:
        res = parse_xlsx_generic(file_bytes, ctx)

        # HOLMCO: END-UNIT suele venir con referencias de equipos -> lo tratamos como alias
        if not res.attributes.empty and not res.parts.empty:
            # detectar keys tipo end-unit
            attr = res.attributes.copy()
            attr["key_l"] = attr["key"].astype(str).str.strip().str.lower()
            end_unit = attr[attr["key_l"].isin(["end-unit", "end unit", "end_unit"])]

            if not end_unit.empty:
                existing = set()
                if not res.aliases.empty:
                    existing = set(
                        (res.aliases["part_number"].astype(str) + "||" + res.aliases["alias_code"].astype(str)).tolist()
                    )

                new_rows = []
                for _, r in end_unit.iterrows():
                    pn = str(r.get("part_number") or "").strip()
                    if not pn:
                        continue
                    main_norm = normalize_pn(pn)
                    raw_val = r.get("value")
                    for a in extract_alias_codes(raw_val):
                        if not a:
                            continue
                        if normalize_pn(a) == main_norm:
                            continue
                        key = pn + "||" + str(a).strip()
                        if key in existing:
                            continue
                        existing.add(key)
                        new_rows.append({
                            "part_number": pn,
                            "alias_code": str(a).strip(),
                            "source": "END_UNIT",
                            "source_file": ctx.filename,
                            "source_sheet": r.get("source_sheet"),
                        })
                if new_rows:
                    res.aliases = pd.concat([res.aliases, pd.DataFrame(new_rows)], ignore_index=True) if not res.aliases.empty else pd.DataFrame(new_rows)

        res.parser_name = self.name
        return res

register(HolmcoXlsxParser())
