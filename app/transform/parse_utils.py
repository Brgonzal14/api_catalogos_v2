from __future__ import annotations
import re
from typing import Any, Optional, Tuple, List

import pandas as pd

_norm_re = re.compile(r"[^A-Za-z0-9]+")
_tier_range_re = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")
_tier_num_re = re.compile(r"^\s*(\d+)\s*$")
_moq_re = re.compile(r"\bmoq\b[^0-9]*(\d+)", flags=re.IGNORECASE)

def normalize_pn(s: str) -> str:
    if not s:
        return ""
    return _norm_re.sub("", str(s).upper().strip())

def normalize_part_number(code: str) -> Tuple[str, str]:
    if code is None:
        return "", ""
    code = str(code).strip()
    if not code:
        return "", ""
    first_token = code.split()[0] if code.split() else code
    root = first_token.split("-")[0] if first_token else ""
    return code, root

def clean_multiline_cell(value: Any) -> Any:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return value
    except Exception:
        pass
    if isinstance(value, str):
        s = value.replace("\xa0", " ").strip()
        if "\n" in s:
            parts = [p.strip() for p in s.splitlines() if p.strip()]
            uniq: List[str] = []
            for p in parts:
                if p not in uniq:
                    uniq.append(p)
            s = " ".join(uniq)
        return s
    return value

def parse_tier_header(header: Any) -> Tuple[Optional[int], Optional[int]]:
    if header is None:
        return (None, None)
    if isinstance(header, (int, float)) and not pd.isna(header):
        if float(header).is_integer():
            header = str(int(header))
        else:
            header = str(header)
    s = str(header).strip()
    if not s:
        return (None, None)
    m = _tier_range_re.match(s)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    m = _tier_num_re.match(s)
    if m:
        return (int(m.group(1)), None)
    return (None, None)

def parse_qty_range(text: str) -> Tuple[Optional[int], Optional[int]]:
    if text is None:
        return (None, None)
    t = str(text).strip().replace(" ", "")
    if not t:
        return (None, None)
    if t.startswith(">"):
        nums = re.findall(r"\d+", t)
        if nums:
            return (int(nums[0]) + 1, None)
        return (None, None)
    m = re.match(r"(\d+)-(\d+)", t)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    if t.isdigit():
        n = int(t)
        return (n, n)
    return (None, None)

def parse_moq_from_text(text: Any) -> Optional[int]:
    if text is None:
        return None
    s = str(text)
    m = _moq_re.search(s)
    return int(m.group(1)) if m else None

def parse_price_value(price_value: Any) -> Optional[float]:
    try:
        import math
        if isinstance(price_value, float) and math.isnan(price_value):
            return None
    except Exception:
        pass
    if price_value is None:
        return None
    s = str(price_value).strip()
    if not s:
        return None
    if s.lower() in ("nan", "none", "null", "<na>"):
        return None
    low = s.lower()
    if low.startswith("price and leadtime"):
        return None
    if "on request" in low or low in ("onrequest", "on-request", "request"):
        return None

    cleaned = (
        s.replace("€", "")
         .replace("$", "")
         .replace("£", "")
         .replace("usd", "")
         .replace("eur", "")
         .replace("gbp", "")
         .replace("\xa0", " ")
         .strip()
    )
    cleaned = cleaned.replace(" ", "")
    # european vs us
    if "." in cleaned and "," in cleaned:
        last_dot = cleaned.rfind(".")
        last_comma = cleaned.rfind(",")
        if last_comma > last_dot:
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        parts = cleaned.split(",")
        if len(parts[-1]) == 2:
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")

    cleaned = re.sub(r"[^0-9\.-]+", "", cleaned)
    if not cleaned or cleaned in ("-", "."):
        return None
    try:
        return float(cleaned)
    except Exception:
        return None
