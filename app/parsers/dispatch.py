from __future__ import annotations
import os
from .types import ParseContext, ParseResult
from .registry import PARSERS

# Ensure vendor parsers are registered
from .pdf import vendors  # noqa: F401
from .xlsx import vendors  # noqa: F401

def _best_parser(ctx: ParseContext, head: bytes):
    candidates = [p for p in PARSERS if ctx.ext in p.exts]
    if not candidates:
        return None, 0
    ranked = sorted(candidates, key=lambda p: p.sniff(ctx, head), reverse=True)
    best = ranked[0]
    score = best.sniff(ctx, head)
    return best, score

def parse_file(file_bytes: bytes, filename: str, supplier_name: str = "") -> ParseResult:
    ext = os.path.splitext(filename or "")[1].lower().strip()
    head = file_bytes[:9000]

    first_text = ""
    if ext == ".pdf":
        from .pdf.common import first_page_text_best
        first_text = first_page_text_best(file_bytes) or ""

    ctx = ParseContext(filename=filename or "", supplier_name=supplier_name or "", ext=ext, first_text=first_text)

    parser, score = _best_parser(ctx, head)
    if parser is None or score < 30:
        # fallback generic by ext
        if ext == ".pdf":
            from .pdf.generic import GenericPdfTableParser
            parser = GenericPdfTableParser()
        elif ext in (".xlsx", ".xls"):
            from .xlsx.generic import GenericXlsxParser
            parser = GenericXlsxParser()
        else:
            raise ValueError(f"Extensión no soportada: {ext}")

    result = parser.parse(file_bytes, ctx)
    result.parser_name = getattr(parser, "name", result.parser_name) or result.parser_name
    return result
