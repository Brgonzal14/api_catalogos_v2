from __future__ import annotations

import io
from typing import Optional, List
import pandas as pd

def iter_text_pages_pypdf(pdf_bytes: bytes, max_pages: Optional[int] = None):
    """Itera texto por página usando PyPDF2 (o pypdf)."""
    PdfReader = None
    err_1 = None
    try:
        from PyPDF2 import PdfReader as _PdfReader  # type: ignore
        PdfReader = _PdfReader
    except Exception as e1:
        err_1 = e1
        try:
            from pypdf import PdfReader as _PdfReader  # type: ignore
            PdfReader = _PdfReader
        except Exception as e2:
            raise ImportError(
                "Se requiere PyPDF2 o pypdf para extraer texto de PDFs. "
                f"Detalle PyPDF2: {err_1} | Detalle pypdf: {e2}"
            )

    reader = PdfReader(io.BytesIO(pdf_bytes))

    # algunos PDFs pueden venir cifrados
    try:
        if getattr(reader, "is_encrypted", False):
            try:
                reader.decrypt("")
            except Exception:
                pass
    except Exception:
        pass

    n = len(reader.pages)
    if max_pages is not None:
        n = min(n, max_pages)

    for i in range(n):
        yield (reader.pages[i].extract_text() or "")

def first_page_text_pypdf(pdf_bytes: bytes) -> str:
    for t in iter_text_pages_pypdf(pdf_bytes, max_pages=1):
        return t or ""
    return ""

def first_page_text_pdfplumber(pdf_bytes: bytes) -> str:
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            if not pdf.pages:
                return ""
            return (pdf.pages[0].extract_text() or "")
    except Exception:
        return ""

def first_page_text_best(pdf_bytes: bytes) -> str:
    # PyPDF suele ser MUCHO más rápido. Usar pdfplumber solo si el texto es muy corto.
    t1 = first_page_text_pypdf(pdf_bytes) or ""
    if len(t1.strip()) >= 80:
        return t1
    t2 = first_page_text_pdfplumber(pdf_bytes) or ""
    return t2 if len(t2.strip()) > len(t1.strip()) else t1

def read_pdf_tables(pdf_bytes: bytes) -> pd.DataFrame:
    """Lee todas las tablas de un PDF con pdfplumber y concatena en DF bruto."""
    try:
        import pdfplumber
    except ImportError as e:
        raise ImportError(f"Falta pdfplumber para leer tablas PDF: {e}")

    all_rows: List[List[Optional[str]]] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables() or []
            for table in tables:
                if not table:
                    continue
                for row in table:
                    all_rows.append(row)

    if not all_rows:
        return pd.DataFrame()

    max_len = max(len(r) for r in all_rows)
    norm_rows = [list(r) + [None] * (max_len - len(r)) for r in all_rows]
    return pd.DataFrame(norm_rows)
