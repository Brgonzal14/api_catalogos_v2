from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, List, Protocol
from .types import ParseContext, ParseResult

class CatalogParser(Protocol):
    name: str
    exts: tuple[str, ...]

    def sniff(self, ctx: ParseContext, head: bytes) -> int:
        ...

    def parse(self, file_bytes: bytes, ctx: ParseContext) -> ParseResult:
        ...

PARSERS: List[CatalogParser] = []

def register(parser: CatalogParser) -> CatalogParser:
    PARSERS.append(parser)
    return parser
