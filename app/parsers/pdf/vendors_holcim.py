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


def _parse_holcim_qty_range(qty_raw: str, moq_raw: str) -> Tuple[Optional[int], Optional[int], str]:
    """Devuelve (min_qty, max_qty, qty_tag)."""
    q = (qty_raw or "").strip()
    ql = q.lower()
    moq = (moq_raw or "").strip()

    def moq_int() -> Optional[int]:
        m = re.search(r"\d+", moq.replace(".", ""))
        return int(m.group(0)) if m else None

    # All / ea -> usamos MOQ como mínimo
    if ql in ("all", "ea", "each"):
        return moq_int() or 1, None, q

    m = re.search(r"^(\d+)\s*to\s*(\d+)$", ql)
    if m:
        return int(m.group(1)), int(m.group(2)), q

    m = re.search(r"^(\d+)\s*-\s*(\d+)$", ql)
    if m:
        return int(m.group(1)), int(m.group(2)), q

    m = re.search(r"^(\d+)\s*(?:and\s+up|\+)$", ql)
    if m:
        return int(m.group(1)), None, q

    m = re.search(r"^>\s*(\d+)$", ql)
    if m:
        return int(m.group(1)), None, q

    m = re.search(r"^(\d+)$", ql)
    if m:
        return int(m.group(1)), None, q

    # fallback: MOQ
    return moq_int() or None, None, q



def parse_pdf_holcim(pdf_bytes: bytes) -> pd.DataFrame:
    """
    HOLCIM Pricing 2026 (PDF de 1 página):
    Extrae productos y price-tiers desde texto (pdfplumber).
    - Maneja rangos "36 to 60", "60-108", "940 and up", "> 81", y "All/ea".
    - Conserva MOQ y un resumen de tiers como atributo (comments) en la primera fila del producto.
    """
    text = first_page_text_pdfplumber(pdf_bytes) or first_page_text_pypdf(pdf_bytes) or ""
    lines = [ln.strip() for ln in text.splitlines() if ln and ln.strip()]

    # modo de unidad según encabezado
    unit_mode = None  # "Each" o "sqm"

    # producto actual + acumuladores
    current_product: Optional[str] = None
    product_info: Dict[str, Dict[str, Any]] = {}  # product -> {tiers:[], notes:set(), unit_mode:str}
    product_order: List[str] = []

    row_re = re.compile(
        r"^(?P<left>.+?)\s+(?P<qty>(?:All|ea|EA|each|EACH|>\s*\d+|\d+\s*to\s*\d+|\d+\s*-\s*\d+|\d+\s*and\s*up|\d+\s*\+|\d+))\s+€\s*(?P<price>\d[\d\.]*,\d{2}|\d[\d,]*\.\d{2})\s+(?P<moq>.+)$"
    )

    def is_noise_line(s: str) -> bool:
        sl = s.lower()
        if sl.startswith("hansair") or sl.startswith("pricing table") or sl.startswith("effective for"):
            return True
        if sl.startswith("floorsil products") or sl.startswith("proprietary information"):
            return True
        if sl.startswith("this document contains") or sl.startswith("this information shall"):
            return True
        if sl.startswith("name:") or sl.startswith("title:") or sl.startswith("date:"):
            return True
        if sl.startswith("qty € price"):
            return True
        return False

    for ln in lines:
        # detect unit mode BEFORE skipping headers/noise
        if "price / each" in ln.lower():
            unit_mode = "Each"
            continue
        if "price per sqm" in ln.lower():
            unit_mode = "sqm"
            continue

        if is_noise_line(ln):
            continue

        m = row_re.match(ln)
        if not m:
            continue

        left = m.group("left").strip()
        qty_raw = m.group("qty").strip()
        price_raw = m.group("price").strip()
        moq_raw = m.group("moq").strip()

        # algunas líneas son "packaging" y pertenecen al producto anterior
        left_l = left.lower()
        is_packaging = (
            current_product is not None
            and (
                left_l.startswith("310ml")
                or left_l.startswith("490ml")
                or left_l.endswith("per tube")
                or "tubes per carton" in left_l
                or left_l.endswith("per carton")
            )
        )

        if (not is_packaging) or current_product is None:
            current_product = left
            if current_product not in product_info:
                product_info[current_product] = {"tiers": [], "notes": set(), "unit_mode": unit_mode}
                product_order.append(current_product)
        else:
            # guardamos nota de packaging
            product_info[current_product]["notes"].add(left)

        # tier
        min_q, max_q, qty_tag = _parse_holcim_qty_range(qty_raw, moq_raw)
        product_info[current_product]["tiers"].append(
            {
                "min_qty": min_q,
                "max_qty": max_q,
                "price": price_raw,
                "moq": moq_raw,
                "qty_tag": qty_tag,
                "unit_mode": unit_mode,
                "packaging_note": (left if is_packaging else None),
            }
        )

    # construir filas
    out_rows: List[Dict[str, Any]] = []
    for product in product_order:
        info = product_info[product]
        tiers = info["tiers"]

        # resumen legible (guardado como atributo en la primera fila)
        summary_lines = []
        if info["notes"]:
            summary_lines.append("Packaging: " + " | ".join(sorted(info["notes"])))
        for t in tiers:
            rng = t["qty_tag"]
            summary_lines.append(f"{rng} -> € {t['price']} (MOQ: {t['moq']})")
        comments_summary = "\n".join(summary_lines).strip() if summary_lines else None

        for j, t in enumerate(tiers):
            out_rows.append(
                {
                    "part_number": product,
                    "description": product if j == 0 else None,
                    "price": t["price"],
                    "currency": "EUR",
                    "min_qty": t["min_qty"],
                    "max_qty": t["max_qty"],
                    "unit_code": t["unit_mode"] or info.get("unit_mode") or None,
                    "moq": t["moq"],
                    "qty_range": t["qty_tag"],
                    "comments": comments_summary if j == 0 else None,
                }
            )

    if not out_rows:
        return pd.DataFrame()

    df = pd.DataFrame(out_rows)

    # limpiar precio
    df["price"] = df["price"].apply(parse_price_value)

    # eliminar duplicados exactos
    df = df.drop_duplicates(subset=["part_number", "min_qty", "max_qty", "price", "currency", "unit_code", "moq", "qty_range"])

    return df



class HolcimPdfParser:
    name = "pdf_holcim"
    exts = (".pdf",)

    def sniff(self, ctx: ParseContext, head: bytes) -> int:
        f = (ctx.filename or '').lower()
        t = (ctx.first_text or '').lower()
        s = 0
        if ('holcim' in f) or ('holcim' in t) or ('holcim' in (ctx.supplier_name or '').lower()): s += 80
        if ('pricing table' in f) or ('pricing table' in t) or ('pricing table' in (ctx.supplier_name or '').lower()): s += 30
        if ('floorsil' in f) or ('floorsil' in t) or ('floorsil' in (ctx.supplier_name or '').lower()): s += 20
        if ('airfloor' in f) or ('airfloor' in t) or ('airfloor' in (ctx.supplier_name or '').lower()): s += 20
        return s

    def parse(self, file_bytes: bytes, ctx: ParseContext) -> ParseResult:
        raw = parse_pdf_holcim(file_bytes)
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

register(HolcimPdfParser())
