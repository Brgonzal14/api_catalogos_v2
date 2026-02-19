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



def _parse_qty_token(token: str) -> Tuple[Optional[int], Optional[str]]:
    """Token tipo '100St.' / '100,0' -> (100, 'St.')"""
    if token is None:
        return (None, None)
    s = str(token).strip()
    if not s:
        return (None, None)
    m = re.match(r"^(?P<qty>\d+(?:[\.,]\d+)?)\s*(?P<u>[A-Za-z\.]+)?$", s)
    if not m:
        return (None, None)
    qty_raw = m.group("qty")
    u = m.group("u")
    # qty
    try:
        q = float(qty_raw.replace(".", "").replace(",", "."))
        qty_i = int(round(q))
        return (qty_i, u)
    except Exception:
        return (None, u)



def parse_pdf_stuker_sac(pdf_bytes: bytes) -> pd.DataFrame:
    """
    Parser STUKERJURGEN SAC Products (PDF por texto).

    PDF suele venir como:
      P/N  Bauteil-Nr. Variante  Bezeichnung  Länge(mm)  Menge  Unit  EUR/Unit
      20014 ABS0236    A 2-11    Einhängeprofil 4500     100,0 m36,10

    Requerimiento clave: guardar el código completo "ABS0236 A 2-11" (Bauteil-Nr. + Variante).
    Guardamos el P/N numérico como article_no.
    """
    rows: List[Dict[str, Any]] = []
    for page_text in iter_text_pages_pypdf(pdf_bytes):
        for raw in (page_text or "").splitlines():
            line = raw.replace("\xa0", " ").strip()
            if not line:
                continue

            low = line.lower()

            # saltar encabezados / notas
            if low.startswith("p/n") or "bauteil-nr" in low or "bezeichnung" in low or "pricelist" in low:
                continue
            if "sales conditions" in low or low.startswith("- ") or low.startswith("-"):
                continue

            # debe empezar con un P/N numérico
            if not re.match(r"^\d{4,}\s+\S+", line):
                continue

            # normalizar "m on request" => on request
            line_norm = re.sub(r"\bmon\s+request\b", "on request", line, flags=re.IGNORECASE)

            # Caso "on request"
            if "on request" in line_norm.lower():
                # quitamos la cola "on request"
                base = re.sub(r"\bon\s+request\b", "", line_norm, flags=re.IGNORECASE).strip()
                toks = base.split()
                if len(toks) < 3:
                    continue
                article_no = toks[0]
                bauteil = toks[1]

                # intentar extraer variante + descripción + (length) + (min_qty) + (uom)
                tail = toks[2:]

                # extraer unidad al final (ej: m, St., EA) si existe
                unit_code = None
                if tail and re.fullmatch(r"[A-Za-z\.]{1,6}", tail[-1]):
                    unit_code = tail[-1]
                    tail = tail[:-1]

                # buscar largo (mm) al final si existe
                length_mm = None
                for j in range(len(tail) - 1, -1, -1):
                    if re.fullmatch(r"\d+(?:[\.,]\d+)?", tail[j]):
                        length_mm = tail[j]
                        tail = tail[:j]  # antes del length queda variante+desc
                        break

                # separar variante/desc por heurística
                var_toks: List[str] = []
                desc_toks: List[str] = []
                var_re = re.compile(r"^[A-Z0-9][A-Z0-9\-]*$")

                k = 0
                while k < len(tail):
                    tok = tail[k]
                    if var_re.match(tok) and not re.search(r"[a-zäöüß]", tok):
                        var_toks.append(tok)
                        k += 1
                    else:
                        break
                desc_toks = tail[k:]

                variante = " ".join(var_toks).strip() or None
                part_number = " ".join([bauteil] + var_toks).strip() if var_toks else bauteil
                description = " ".join(desc_toks).strip() if desc_toks else ""

                rows.append(
                    {
                        "article_no": article_no,
                        "bauteil_nr": bauteil,
                        "variante": variante,
                        "part_number": part_number,
                        "description": description,
                        "length_mm": length_mm,
                        "min_qty": None,
                        "unit_code": unit_code,
                        "price": None,
                        "currency": "EUR",
                        "pricing_note": "on request",
                    }
                )
                continue

            # Caso con precio al final: captura unidad pegada al precio (m36,10 / St.22,75 / ...)
            m_price = re.search(r"(?P<pre>[A-Za-z\.]+)?(?P<price>\d+(?:\.\d{3})*(?:,\d+))\s*$", line_norm)
            if not m_price:
                continue

            unit_from_price = (m_price.group("pre") or "").strip() or None
            price = parse_price_value(m_price.group("price"))

            left = line_norm[: m_price.start()].strip()
            toks = left.split()
            if len(toks) < 5:
                continue

            article_no = toks[0]
            bauteil = toks[1]

            # los últimos tokens suelen ser: <length_mm> <qty>
            length_token = toks[-2]
            qty_token = toks[-1]
            length_mm = length_token if re.fullmatch(r"\d+(?:[\.,]\d+)?", length_token) else None

            qty_val, qty_uom = _parse_qty_token(qty_token)

            mid = toks[2:-2]  # variante + descripción

            var_re = re.compile(r"^[A-Z0-9][A-Z0-9\-]*$")
            var_toks: List[str] = []
            desc_toks: List[str] = []
            k = 0
            while k < len(mid):
                tok = mid[k]
                if var_re.match(tok) and not re.search(r"[a-zäöüß]", tok):
                    var_toks.append(tok)
                    k += 1
                else:
                    break
            desc_toks = mid[k:]

            variante = " ".join(var_toks).strip() or None
            part_number = " ".join([bauteil] + var_toks).strip() if var_toks else bauteil
            description = " ".join(desc_toks).strip() if desc_toks else ""

            uom = unit_from_price or qty_uom

            rows.append(
                {
                    "article_no": article_no,
                    "bauteil_nr": bauteil,
                    "variante": variante,
                    "part_number": part_number,
                    "description": description,
                    "length_mm": length_mm,
                    "min_qty": qty_val,
                    "unit_code": uom,
                    "price": price,
                    "currency": "EUR",
                }
            )

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows)



class StukerSacPdfParser:
    name = "pdf_stuker_sac"
    exts = (".pdf",)

    def sniff(self, ctx: ParseContext, head: bytes) -> int:
        f = (ctx.filename or '').lower()
        t = (ctx.first_text or '').lower()
        s = 0
        if ('sac products' in f) or ('sac products' in t) or ('sac products' in (ctx.supplier_name or '').lower()): s += 60
        if ('bauteil-nr' in f) or ('bauteil-nr' in t) or ('bauteil-nr' in (ctx.supplier_name or '').lower()): s += 40
        if ('bezeichnung' in f) or ('bezeichnung' in t) or ('bezeichnung' in (ctx.supplier_name or '').lower()): s += 20
        if ('länge' in f) or ('länge' in t) or ('länge' in (ctx.supplier_name or '').lower()): s += 20
        if ('stuker' in f) or ('stuker' in t) or ('stuker' in (ctx.supplier_name or '').lower()): s += 20
        return s

    def parse(self, file_bytes: bytes, ctx: ParseContext) -> ParseResult:
        raw = parse_pdf_stuker_sac(file_bytes)
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

register(StukerSacPdfParser())
