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


def _albany_clean_line(s: str) -> str:
    s = (s or "").replace("\xa0", " ").strip()
    # arreglos típicos de extracción: ")1" -> ") 1" y "-AIR" -> "- AIR"
    s = re.sub(r"\)(?=\d)", ") ", s)
    s = re.sub(r"(?<=\s)-(?=[A-Za-z])", "- ", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s



def _extract_pn_note(partnumber: str) -> Tuple[str, str]:
    """Separa un posible comentario pegado al PN y lo manda a nota.

    Ejemplos típicos:
      - "PN123 (ON REQUEST)" -> ("PN123", "ON REQUEST")
      - "PN123 ON REQUEST"   -> ("PN123", "ON REQUEST")
      - "PN123*"             -> ("PN123", "*")

    No intenta separar PNs que legítimamente traen espacios (ej: "ABS0236 A 2-11").
    """
    if not partnumber:
        return "", ""

    s = str(partnumber).strip()

    # ( ... ) o [ ... ] al final
    m = re.match(r"^(.*?)(?:\s*[\(\[]([^\)\]]+)[\)\]])\s*$", s)
    if m:
        base = (m.group(1) or "").strip()
        note = (m.group(2) or "").strip()
        if base and note:
            return base, note

    # asteriscos al final
    m2 = re.match(r"^(.*?)(\*+)$", s)
    if m2:
        base = (m2.group(1) or "").strip()
        stars = (m2.group(2) or "").strip()
        if base and stars:
            return base, stars

    # palabras tipo comentario al final (ON REQUEST / CALL FOR PRICE / EXCHANGE / etc)
    tokens = s.split()
    if len(tokens) >= 2:
        keywords = {
            "ON", "REQUEST", "CALL", "FOR", "PRICE", "INFO", "INFORMATION",
            "CONTACT", "QUOTE", "QTY", "MIN", "MOQ", "EXCHANGE", "EXCH", "REPAIR",
            "TBD", "TBA", "N/A", "NA",
        }
        # buscamos una cola que contenga estas palabras
        up = [t.upper() for t in tokens]
        # si la cola contiene 'ON' y 'REQUEST'
        joined = " ".join(up)
        if "ON REQUEST" in joined or "CALL" in up or "QUOTE" in up or "EXCHANGE" in up or "REPAIR" in up:
            # tomamos desde el primer keyword hasta el final
            first_kw = None
            for i, t in enumerate(up):
                if t in keywords:
                    first_kw = i
                    break
            if first_kw is not None and first_kw > 0:
                base = " ".join(tokens[:first_kw]).strip()
                note = " ".join(tokens[first_kw:]).strip()
                # evitar separar sufijos simples tipo "A"
                if base and note and (note.upper() in {"ON REQUEST", "CALL", "CALL FOR PRICE", "EXCHANGE", "REPAIR"} or any(k in note.upper() for k in ["ON REQUEST", "CALL", "PRICE", "EXCHANGE", "REPAIR", "QUOTE"])):
                    return base, note

    return s, ""




def parse_pdf_albany(pdf_bytes: bytes) -> pd.DataFrame:
    """
    ALBANY 2026 BOEING SPARES CATALOG (PDF):
    Extrae listado de repuestos desde texto (pypdf).
    Columnas típicas:
      BOEING/INDUSTRY PN | CURRENT ALBANY PN | PREVIOUS ALBANY PN | DESCRIPTION | QTY | UOM | PRICE (USD)

    Además:
      - si hay filas partidas (descripción en una línea y qty/price en la siguiente), las une.
    """
    rows: List[Dict[str, Any]] = []

    for page_text in iter_text_pages_pypdf(pdf_bytes):
        if not page_text or "$" not in page_text:
            continue

        raw_lines = [ln for ln in page_text.splitlines() if ln and ln.strip()]
        i = 0
        while i < len(raw_lines):
            ln = _albany_clean_line(raw_lines[i])

            # saltar fees / líneas no-PN
            low = ln.lower()
            if low.startswith("minimum value of $") or " fee $" in low or low.endswith(" usd."):
                i += 1
                continue

            # match directo (fila completa)
            if _albany_price_pat.search(ln):
                m = _albany_row_re.match(ln)
                if m:
                    gd = m.groupdict()
                    rows.append(
                        {
                            "part_number": gd["current"].strip(),
                            "description": gd["desc"].strip(),
                            "price": gd["price"].strip(),
                            "currency": "USD",
                            "min_qty": gd["qty"].strip(),
                            "unit_code": gd["uom"].strip(),
                            "alias_boeing": (gd["boeing"].strip() if gd["boeing"].strip() != "-" else None),
                            "alias_previous": (gd["previous"].strip() if gd["previous"].strip() != "-" else None),
                        }
                    )
                i += 1
                continue

            # intentar unir con la siguiente línea si trae qty/price
            if i + 1 < len(raw_lines):
                nxt = _albany_clean_line(raw_lines[i + 1])
                combined = (ln + " " + nxt).strip()
                if _albany_price_pat.search(combined):
                    m = _albany_row_re.match(combined)
                    if m:
                        gd = m.groupdict()
                        rows.append(
                            {
                                "part_number": gd["current"].strip(),
                                "description": gd["desc"].strip(),
                                "price": gd["price"].strip(),
                                "currency": "USD",
                                "min_qty": gd["qty"].strip(),
                                "unit_code": gd["uom"].strip(),
                                "alias_boeing": (gd["boeing"].strip() if gd["boeing"].strip() != "-" else None),
                                "alias_previous": (gd["previous"].strip() if gd["previous"].strip() != "-" else None),
                            }
                        )
                        i += 2
                        continue

            i += 1

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    # limpiar tipos
    df["price"] = df["price"].apply(parse_price_value)
    df["min_qty"] = df["min_qty"].apply(lambda x: int(float(x)) if x is not None and str(x) != "nan" and str(x) != "" else None)

    # eliminar duplicados exactos para evitar PriceTiers repetidos
    df = df.drop_duplicates(subset=["part_number", "price", "min_qty", "alias_boeing", "alias_previous", "description"])

    return df


_holcim_price_re = re.compile(r"€\s*(\d[\d\.]*,\d{2}|\d[\d,]*\.\d{2})")



class AlbanyPdfParser:
    name = "pdf_albany"
    exts = (".pdf",)

    def sniff(self, ctx: ParseContext, head: bytes) -> int:
        f = (ctx.filename or '').lower()
        t = (ctx.first_text or '').lower()
        s = 0
        if ('albany' in f) or ('albany' in t) or ('albany' in (ctx.supplier_name or '').lower()): s += 80
        if ('boeing' in f) or ('boeing' in t) or ('boeing' in (ctx.supplier_name or '').lower()): s += 20
        if ('spares' in f) or ('spares' in t) or ('spares' in (ctx.supplier_name or '').lower()): s += 20
        if ('catalog' in f) or ('catalog' in t) or ('catalog' in (ctx.supplier_name or '').lower()): s += 20
        return s

    def parse(self, file_bytes: bytes, ctx: ParseContext) -> ParseResult:
        raw = parse_pdf_albany(file_bytes)
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

register(AlbanyPdfParser())
