from __future__ import annotations

import io
import re
import inspect
from typing import Any, Dict, List, Optional

import pandas as pd

from ..registry import register
from ..types import ParseContext, ParseResult


def _norm_col(c: Any) -> str:
    s = str(c).replace("\xa0", " ").replace("\n", " ").replace("\r", " ")
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def _find_header_row(df_raw: pd.DataFrame) -> Optional[int]:
    for i, row in df_raw.iterrows():
        for cell in row:
            if isinstance(cell, str) and "part number" in cell.lower():
                return int(i)
    return None


def _find_col(df: pd.DataFrame, contains_all: List[str]) -> Optional[str]:
    # devuelve nombre real de columna cuyo nombre normalizado contiene todas las palabras
    for c in df.columns:
        k = _norm_col(c)
        if all(word.lower() in k for word in contains_all):
            return str(c)
    return None


def _guess_currency_from_file(filename: str) -> str:
    fn = (filename or "").lower()
    if "eur" in fn:
        return "EUR"
    return "USD"


def _parse_price(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        if isinstance(v, float) and pd.isna(v):
            return None
    except Exception:
        pass
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return None

    # HOLMCO suele venir "595,00"
    s = s.replace("$", "").replace("USD", "").replace("EUR", "").strip()
    s = s.replace(" ", "")
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")

    m = re.search(r"[-+]?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except Exception:
        return None


def _parse_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        if isinstance(v, float) and pd.isna(v):
            return None
    except Exception:
        pass
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return None
    m = re.search(r"\d+", s)
    return int(m.group(0)) if m else None


def _extract_alias_codes(value: Any) -> List[str]:
    """END-UNIT: 'AAA (BBB)' => ['AAA','BBB'] y soporta comas/saltos de línea."""
    if value is None:
        return []
    s = str(value).strip()
    if not s or s.lower() in ("nan", "none", "null") or s == "-":
        return []

    m = re.match(r"^\s*([^\s(]+)\s*\(([^)]+)\)\s*$", s)
    if m:
        a = m.group(1).strip()
        b = m.group(2).strip()
        out = [x for x in [a, b] if x and x != "-"]
        return out

    parts = re.split(r"[;,\n\t]+", s)
    out: List[str] = []
    for p in parts:
        p = p.strip().strip(",")
        if not p or p == "-":
            continue
        out.append(p)
    return list(dict.fromkeys(out))


def _make_parse_result(
    parser_name: str,
    *,
    parts_df: pd.DataFrame,
    tiers_df: pd.DataFrame,
    aliases_df: pd.DataFrame,
    attributes_df: pd.DataFrame,
    source_file: str,
) -> ParseResult:
    """
    Crea ParseResult pasando solo los kwargs que existan en tu ParseResult real.
    """
    candidates: Dict[str, Any] = {
        "parser_name": parser_name,
        "source_file": source_file,
        "parts": parts_df,
        "parts_df": parts_df,
        "tiers": tiers_df,
        "tiers_df": tiers_df,
        "aliases": aliases_df,
        "aliases_df": aliases_df,
        "attributes": attributes_df,
        "attributes_df": attributes_df,
    }

    sig = inspect.signature(ParseResult)
    allowed = set(sig.parameters.keys())
    kwargs = {k: v for k, v in candidates.items() if k in allowed}

    result = ParseResult(**kwargs)  # type: ignore

    # set parser_name si existe como atributo
    if hasattr(result, "parser_name") and not getattr(result, "parser_name", None):
        try:
            setattr(result, "parser_name", parser_name)
        except Exception:
            pass

    return result


class HolmcoXlsxParser:
    name = "holmco_xlsx"
    exts = (".xlsx", ".xls")

    def sniff(self, ctx: ParseContext, head: bytes) -> int:
        score = 0
        fn = (ctx.filename or "").lower()
        sup = (ctx.supplier_name or "").lower()
        if "holmco" in fn:
            score += 60
        if "holmco" in sup:
            score += 60
        if b"PART NUMBER" in head:
            score += 10
        if b"END-UNIT" in head or b"END UNIT" in head:
            score += 10
        return score

    def parse(self, file_bytes: bytes, ctx: ParseContext) -> ParseResult:
        df_raw = pd.read_excel(io.BytesIO(file_bytes), sheet_name=0, header=None)
        header_row = _find_header_row(df_raw)
        if header_row is None:
            raise ValueError("HOLMCO: No se encontró la fila de encabezado (PART NUMBER).")

        df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=0, header=header_row)
        df = df.where(pd.notnull(df), None)

        # columnas clave (por nombre aproximado)
        pn_col = _find_col(df, ["part", "number"])
        desc_col = _find_col(df, ["description"])
        end_unit_col = _find_col(df, ["end", "unit"]) or _find_col(df, ["end-unit"])
        pricebreak_col = _find_col(df, ["price", "break"])  # "PRICE BREAK AT PIECES"
        package_col = _find_col(df, ["package", "qty"])
        remark_col = _find_col(df, ["remark"])
        lead_col = _find_col(df, ["lead", "time"])

        # precio: columna con "price" y "usd/eur"
        price_col = None
        for c in df.columns:
            k = _norm_col(c)
            if "price" in k and ("usd" in k or "eur" in k) and ("break" not in k):
                price_col = str(c)
                break

        if pn_col is None:
            raise ValueError("HOLMCO: no se encontró columna PART NUMBER.")

        currency = _guess_currency_from_file(ctx.filename or "")

        parts_rows: List[Dict[str, Any]] = []
        tiers_rows: List[Dict[str, Any]] = []
        alias_rows: List[Dict[str, Any]] = []

        for _, r in df.iterrows():
            pn = r.get(pn_col)
            if pn is None:
                continue
            pn_str = re.sub(r"\s+", "", str(pn).strip())  # quita espacios internos invisibles
            if not pn_str or pn_str.lower() in ("nan", "none", "null"):
                continue

            desc = str(r.get(desc_col)).strip() if desc_col and r.get(desc_col) is not None else None
            price = _parse_price(r.get(price_col)) if price_col else None

            # MOQ desde "PRICE BREAK AT PIECES" (si no, 1)
            moq = _parse_int(r.get(pricebreak_col)) if pricebreak_col else None
            moq = moq if moq and moq > 0 else 1

            end_unit_raw = str(r.get(end_unit_col)).strip() if end_unit_col and r.get(end_unit_col) is not None else None
            package_qty = _parse_int(r.get(package_col)) if package_col else None
            remark = str(r.get(remark_col)).strip() if remark_col and r.get(remark_col) is not None else None
            lead_time = _parse_int(r.get(lead_col)) if lead_col else None

            # ---- parts_df (lo extra queda como columnas -> upsert lo guarda como attributes) ----
            parts_rows.append(
                {
                    "part_number": pn_str,
                    "description": desc,
                    "currency": currency,
                    "price": price,
                    "min_qty_default": moq,
                    # columnas extra (se guardan como PartAttribute por tu upsert) :contentReference[oaicite:2]{index=2}
                    "end_unit_raw": end_unit_raw,
                    "package_qty": package_qty,
                    "remark": remark,
                    "lead_time": lead_time,
                    "source_file": ctx.filename or "",
                    "parser_name": self.name,
                }
            )

            # ---- tiers_df (1 tramo para que aparezca en UI) ----
            if price is not None:
                tiers_rows.append(
                    {
                        "part_number": pn_str,
                        "min_qty": moq,
                        "max_qty": None,
                        "unit_price": price,
                        "currency": currency,
                        "source_file": ctx.filename or "",
                    }
                )

            # ---- aliases_df desde END-UNIT ----
            if end_unit_raw:
                for code in _extract_alias_codes(end_unit_raw):
                    if code and code != pn_str:
                        alias_rows.append(
                            {
                                "part_number": pn_str,
                                "alias_code": code,
                                "source": "END_UNIT",
                                "source_file": ctx.filename or "",
                            }
                        )

        parts_df = pd.DataFrame(parts_rows)
        tiers_df = pd.DataFrame(tiers_rows)
        aliases_df = pd.DataFrame(alias_rows) if alias_rows else pd.DataFrame()
        if not aliases_df.empty:
            aliases_df = aliases_df.drop_duplicates(subset=["part_number", "alias_code"]).reset_index(drop=True)

        # No necesitamos attributes_df porque las columnas extra ya van en parts_df
        attributes_df = pd.DataFrame()

        return _make_parse_result(
            self.name,
            parts_df=parts_df,
            tiers_df=tiers_df,
            aliases_df=aliases_df,
            attributes_df=attributes_df,
            source_file=ctx.filename or "",
        )


register(HolmcoXlsxParser())