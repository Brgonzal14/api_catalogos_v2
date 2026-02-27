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


def _iter_pages_text(pdf_bytes: bytes):
    """
    Extrae texto página por página.
    Intenta pdfplumber primero (más robusto con tablas/columnas), 
    con fallback a PyPDF si pdfplumber no está disponible.
    """
    try:
        import pdfplumber
        import io as _io
        with pdfplumber.open(_io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                # Extraer texto con tolerancia extra para columnas alineadas
                text = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
                yield text
        return
    except ImportError:
        pass
    except Exception:
        pass

    # Fallback: pypdf
    yield from iter_text_pages_pypdf(pdf_bytes)


def _parse_vincorion_line(raw: str, _ROW_RE, _SKIP_EXACT, _SKIP_START, _SECTION_HEADERS):
    """Intenta parsear una línea del PDF Vincorion. Devuelve dict o None."""
    if not raw:
        return None
    low = raw.lower()

    if low in _SKIP_EXACT:
        return None
    if any(low.startswith(p) for p in _SKIP_START):
        return None
    if low in _SECTION_HEADERS:
        return None

    m = _ROW_RE.match(raw)
    if not m:
        return None

    ata  = m.group("ata")
    pn   = m.group("pn").strip()
    rest = m.group("rest").strip()

    if not pn:
        return None

    # LOR / QOR (precio bajo pedido)
    is_lor_qor = bool(re.search(r"\bLOR\b|\bQOR\b", rest, re.IGNORECASE))

    if is_lor_qor:
        desc_raw = re.sub(r"\s*\bLOR\b.*$", "", rest, flags=re.IGNORECASE).strip()
        desc_raw = re.sub(r"\s*\bQOR\b.*$", "", desc_raw, flags=re.IGNORECASE).strip()
        lead_time = None
        mlt = re.search(r"(\d+\s*(?:day|days|week|weeks|month|months))",
                        desc_raw, re.IGNORECASE)
        if mlt:
            lead_time = mlt.group(1)
            desc = desc_raw[:mlt.start()].strip()
        else:
            desc = desc_raw
        return {
            "ata": ata, "part_number": pn, "description": desc,
            "lead_time": lead_time, "price": None,
            "currency": "USD", "pricing_note": "LOR/QOR",
        }

    # Precio numérico: último token
    toks = rest.split()
    if not toks:
        return None

    price = parse_price_value(toks[-1])
    if price is None and len(toks) >= 2:
        price = parse_price_value(toks[-2])
        if price is not None:
            toks = toks[:-1]

    if price is None:
        return None

    rest_wo_price = " ".join(toks[:-1]).strip()
    lead_time = None
    if re.search(r"\bon\s+request\b", rest_wo_price, re.IGNORECASE):
        lead_time = "on request"
        desc = re.sub(r"\bon\s+request\b", "", rest_wo_price, re.IGNORECASE).strip()
    else:
        mlt = re.search(r"(\d+\s*(?:day|days|week|weeks|month|months))",
                        rest_wo_price, re.IGNORECASE)
        if mlt:
            lead_time = mlt.group(1)
            desc = rest_wo_price[:mlt.start()].strip()
        else:
            desc = rest_wo_price

    return {
        "ata": ata, "part_number": pn, "description": desc,
        "lead_time": lead_time, "price": price,
        "currency": "USD", "pricing_note": None,
    }


def parse_pdf_vincorion(pdf_bytes: bytes) -> pd.DataFrame:
    """VINCORION Airbus spares: parseo por texto (ATA | Partnumber | Designation | Lead Time | Price US-$).

    Maneja:
    - Precios numéricos normales (ej. 1.492,89 o 19.485,27)
    - LOR / QOR (Lead/Quote On Request) -> price=None, se incluye igual con pricing_note
    - Múltiples secciones por página (Control Units, Floor Panel Heater, Trolley Lift, etc.)
    - Part numbers con guiones, puntos, sufijos alfanuméricos y sufijos -00
    - Usa pdfplumber para extracción de texto (más robusto con columnas)
    """
    rows = []

    _SKIP_EXACT = {
        "annual price catalogue 2026", "issue 1", "spare parts",
        "general conditions", "replacements", "table of content",
        "emergency contacts", "loan agreement",
        "vincorion advanced systems gmbh", "vincorion advanced system",
        "feldstrasse 155", "d-22880 wedel", "cage code: d3683",
    }
    _SKIP_START = (
        "phone:", "fax:", "email:", "advanced-systems", "page ",
        "appendix", "please note",
        "1.", "2.", "3.", "4.", "5.", "6.", "7.",  # secciones de condiciones
    )
    _SECTION_HEADERS = {
        "control units & panels", "floor panel heater", "heater units",
        "indicators", "nipple, heated & unheated, accessories",
        "nipple, heated & unheated and accessories",
        "trolley lift conveyance system a380-800",
        "trolley lift system a380",
        "ata partnumber designation of item lead time price  us-$",
        "ata partnumber designation of item lead time price us-$",
        "superseded by",
    }

    _ROW_RE = re.compile(
        r"^(?P<ata>\d{2})\s+"
        r"(?P<pn>[A-Za-z0-9][A-Za-z0-9\-\/\.]*?)"
        r"(?:\s+|(?=[A-Z][a-z]))"
        r"(?P<rest>.+)$"
    )

    header_seen = False
    seen_pns: set = set()  # deduplicar si el PDF tuviera alguna página repetida

    try:
        for page_text in _iter_pages_text(pdf_bytes):
            if not page_text:
                continue

            for line in page_text.splitlines():
                raw = (line or "").strip()
                if not raw:
                    continue
                low = raw.lower()

                # Detectar cabecera de tabla -> activar parseo
                if "ata partnumber" in low and "lead time" in low:
                    header_seen = True
                    continue

                if not header_seen:
                    continue

                result = _parse_vincorion_line(
                    raw, _ROW_RE, _SKIP_EXACT, _SKIP_START, _SECTION_HEADERS
                )
                if result is None:
                    continue

                # Deduplicar (mismo PN puede aparecer en tabla de Replacements sin precio)
                pn_key = result["part_number"]
                if pn_key in seen_pns:
                    # Si ya lo tenemos con precio y este es LOR/QOR, mantener el con precio
                    # Si el nuevo tiene precio y el viejo no, actualizar
                    continue
                seen_pns.add(pn_key)
                rows.append(result)

        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except Exception:
        return pd.DataFrame(rows) if rows else pd.DataFrame()






# ============================================================
#  NUEVOS PDF (ALBANY / HOLCIM)
# ============================================================

_albany_token = r"(?:-|[A-Z0-9][A-Z0-9\-/\.]*\d[A-Z0-9\-/\.]*)"
_albany_row_re = re.compile(
    rf"^\s*(?P<boeing>{_albany_token})\s+(?P<current>{_albany_token})\s+(?P<previous>{_albany_token})\s+"
    r"(?P<desc>.+?)\s+(?P<qty>\d+(?:\.\d+)?)\s+(?P<uom>[A-Za-z\.]+)\s+\$(?P<price>[\d,]+\.\d{2})\s*$"
)
_albany_price_pat = re.compile(r"\$\s*[\d,]+\.\d{2}\b")



class VincorionPdfParser:
    name = "pdf_vincorion"
    exts = (".pdf",)

    def sniff(self, ctx: ParseContext, head: bytes) -> int:
        f = (ctx.filename or '').lower()
        t = (ctx.first_text or '').lower()
        s = 0
        if ('vincorion' in f) or ('vincorion' in t) or ('vincorion' in (ctx.supplier_name or '').lower()): s += 80
        if ('annual price catalogue' in f) or ('annual price catalogue' in t) or ('annual price catalogue' in (ctx.supplier_name or '').lower()): s += 20
        if ('spare parts' in f) or ('spare parts' in t) or ('spare parts' in (ctx.supplier_name or '').lower()): s += 10
        return s

    def parse(self, file_bytes: bytes, ctx: ParseContext) -> ParseResult:
        raw = parse_pdf_vincorion(file_bytes)
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

register(VincorionPdfParser())