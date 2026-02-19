from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd

@dataclass(frozen=True)
class ParseContext:
    filename: str
    supplier_name: str = ""
    ext: str = ""
    first_text: str = ""

@dataclass
class ParseResult:
    # Standard outputs (can be empty DFs)
    parts: pd.DataFrame = field(default_factory=lambda: pd.DataFrame())
    tiers: pd.DataFrame = field(default_factory=lambda: pd.DataFrame())
    aliases: pd.DataFrame = field(default_factory=lambda: pd.DataFrame())
    attributes: pd.DataFrame = field(default_factory=lambda: pd.DataFrame())
    parser_name: str = ""
