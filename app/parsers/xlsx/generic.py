from __future__ import annotations

import io
import re
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from ..registry import register
from ..types import ParseContext, ParseResult
from .common import (
    dedupe_columns,
    detect_header_and_qty_ranges,
    read_xlsx_fallback,
    extract_alias_codes,
    extract_jehier_trailing_code,
    detect_pricing_note,
)
from ...transform.parse_utils import (
    normalize_pn,
    normalize_part_number,
    parse_moq_from_text,
    parse_price_value,
    parse_qty_range,
    parse_tier_header,
    clean_multiline_cell,
)

COLUMN_MAP = {
            # códigos de pieza
            "part number": "part_number", "part_number": "part_number", "part numbep": "part_number",
            "pn": "part_number", "p/n": "part_number", "part-no.": "part_number",
            "part-no": "part_number", "part no.": "part_number",

            # Aerox (N23D)
            "n23d part number": "part_number",
            "aerox part number": "supplier_part_no",
            "2026 distributor price (moc)": "price_distributor",
            "2026 dealer price (non-moc)": "price_dealer",
            "2026 retail price": "price_retail",
            "minimum order quantity": "min_qty",

            # JEHIER
            "part number jehier": "part_number",
            "conditioning unit": "description",
            "price per unit": "price",
            "sales unit": "unit_code",

            # Alemán (STUKERJURGEN / STABILUS)
            "bauteil-nr.": "bauteil_nr", "bauteil-nr": "bauteil_nr",
            "bauteil nr.": "bauteil_nr", "bauteil nr": "bauteil_nr",
            "variante": "variante",
            "bezeichnung": "description",
            "länge (mm)": "length_mm", "länge(mm)": "length_mm", "länge": "length_mm",
            "menge": "min_qty", "ab menge": "min_qty",
            "eur/unit": "price", "eur / unit": "price", "eur pro unit": "price",
            "vk": "price",
            "art.-nr.": "part_number", "art.-nr": "part_number", "art.nr.": "part_number", "art.nr": "part_number",

            # Article / ArticleNo
            "articleno.": "article_no", "article no.": "article_no", "article no": "article_no",

            # columnas ITEM (AIRTEC-BRAIDS)
            "item (standard)": "part_number", "item (other)": "part_number", "item": "part_number",


            # --- NUEVOS PDFs / proveedores ---
            # IPECO (PDF)
            "material": "part_number",
            "price (usd)": "price",
            # KEDDEG / otros: "Distributor Net Price"
            "distributor net price": "price",
            "distributor price": "price",
            "net price": "price",

            # Lufthansa Technik / genérico
            "price in eur": "price",
            "price in usd": "price",
            "price (eur)": "price",

            # MOQ / Leadtime variantes
            "min order qty": "min_qty",
            "min order qty.": "min_qty",
            "min order quantity": "min_qty",
            "minimum order qty": "min_qty",
            "minimum order quantity": "min_qty",
            "lead-time in weeks": "lead_time",
            "lead time in weeks": "lead_time",
            "leadtime in weeks": "lead_time",
            "uom": "unit_code", "uom.": "unit_code", "unit of measure": "unit_code",
            "lead time (days)": "lead_time",
            "of": "package_qty", "per": "per_qty",

            # DIEHL (PDF)
            "pnr": "part_number",
            "price 2026": "price",
            "cur": "currency",
            "unt": "unit_code",
            "moq": "min_qty",
            "spq": "package_qty",
            "ltm": "lead_time",

            # COLLINS / equivalencias
            "standard part number": "standard_part_number",
            "rf part number": "rf_part_number",
            "rf partnumber": "rf_part_number",
            "rf part no": "rf_part_number",

            # descripción
            "descripcion": "description", "description": "description",

            # precio
            "price": "price", "precio": "price", "master $": "price",

            # moneda
            "currency": "currency", "moneda": "currency",

            # cantidades mínimas / máximas
            "min qty": "min_qty", "min_qty": "min_qty", "from qty": "min_qty",
            "to qty": "max_qty", "max qty": "max_qty",
            "quantity": "min_qty", "qty": "min_qty",


            # --- NUEVOS XLSX (PUROLATOR / SAFRAN) ---
            "partnumber": "part_number",
            "lead-time": "lead_time", "leadtime": "lead_time",
            "stock ref": "stock_ref", "stock ref.": "stock_ref",
            "safran stock ref.": "stock_ref",
            "comments": "comments", "comment": "comments", "remarks": "comments", "remark": "comments",
            "uon": "unit_code", "u/o/m": "unit_code", "uom": "unit_code", "uom.": "unit_code",

            # --- NUEVOS PDF (TDI) ---
            "part description": "description",
            "seat model": "seat_model",
            "aircraft": "aircraft",
            "manufacturing": "manufacturing",
            "leadtime": "lead_time",
            "sales price": "price",
            "min buy": "min_qty",

            # --- NUEVOS EXCEL (ADHETEC / AEROX / JEHIER) ---
            "customer pn": "part_number",
            "designation": "description",
            "unit of measure": "unit_code",
            "standard lead time": "lead_time",
            "unit eur prices": "price",
            "unit eur price": "price",

            # AEROX (N23D) Cylinder Price Book
            "n23d part number": "part_number",
            "aerox part number": "supplier_part_no",
            "minimum order quantity": "min_qty",
            "2026 distributor price (moc)": "price",

            # JEHIER HNSR_OFFRE
            "part number jehier": "part_number",
            "conditioning unit": "description",
            "sales unit": "unit_code",
            "price per unit": "price",
            "minimum quantity": "min_qty",


            # --- NUEVOS EXCEL (CML / TORRINGTON) ---
            "cml item number": "part_number",
            "item number": "part_number",
            "mpn": "part_number",
            "price each": "price",
            "price/each": "price",
            "price ea": "price",
            "unit pice": "price",
            "net price": "price",
            "pack size": "package_qty",
            "standard package qty": "package_qty",
            "minimum order qty": "min_qty",
            "minimum order qty.": "min_qty",
            "catalog year": "catalog_year",
            "cage code": "cage_code",
            "manufacturer name": "manufacturer_name",
            "item description": "description",
            # otros posibles
            "unit code": "unit_code", "unit": "unit_code", "lead time": "lead_time",
            "lifecycle": "lifecycle", "cumulative": "cumulative",
        }

def _guess_currency(filename: str) -> str:
    f=(filename or "").lower()
    if "eur" in f or "€" in f:
        return "EUR"
    if "gbp" in f or "£" in f:
        return "GBP"
    return "USD"

class GenericXlsxParser:
    name = "xlsx_generic"
    exts = (".xlsx", ".xls")

    def sniff(self, ctx: ParseContext, head: bytes) -> int:
        # fallback: siempre disponible para xlsx/xls
        return 20

    def parse(self, file_bytes: bytes, ctx: ParseContext) -> ParseResult:
        return parse_xlsx_generic(file_bytes, ctx)

register(GenericXlsxParser())

def parse_xlsx_generic(file_bytes: bytes, ctx: ParseContext) -> ParseResult:
    default_currency = _guess_currency(ctx.filename)

    # Leer hojas
    dfs: List[Tuple[str, pd.DataFrame]] = []
    ext = ctx.ext.lower()
    if ext in (".xlsx", ".xls"):
        try:
            excel_file = pd.ExcelFile(io.BytesIO(file_bytes), engine="openpyxl" if ext==".xlsx" else None)
            for sheet in excel_file.sheet_names:
                tmp = excel_file.parse(sheet)
                if tmp is not None and not tmp.empty:
                    dfs.append((sheet, tmp))
        except Exception:
            # fallback (sheet1)
            dfs = [("sheet1", read_xlsx_fallback(file_bytes))]

    parts_rows: Dict[str, Dict[str, Any]] = {}
    tiers_rows: List[Dict[str, Any]] = []
    alias_rows: List[Dict[str, Any]] = []
    attr_rows: List[Dict[str, Any]] = []

    sup_l = (ctx.supplier_name or "").lower()
    fn_l = (ctx.filename or "").lower()
    is_holcim = ("holcim" in sup_l) or ("holcim" in fn_l)

    for sheet_name, original_df in dfs:
        df, qty_ranges_by_col_lower = detect_header_and_qty_ranges(original_df)
        if df is None or df.empty:
            continue

        # ---- normalizar nombres de columnas (similar a tu main) ----
        normalized_cols: Dict[str, str] = {}
        for col in df.columns:
            raw_name = re.sub(r"\s+", " ", str(col).replace("\xa0", " ").replace("\n", " ").replace("\r", " ")).strip()
            col_key = raw_name.lower()

            if col_key in COLUMN_MAP:
                mapped = COLUMN_MAP[col_key]
            elif col_key.startswith("standard lead time") or ("lead time" in col_key or "leadtime" in col_key):
                mapped = "lead_time"
            elif col_key.startswith("price per unit"):
                mapped = "price"
            elif col_key.startswith("minimum quantity") or col_key.startswith("minimum order quantity") or col_key == "moq":
                mapped = "min_qty"
            elif col_key.startswith("unit of measure") or col_key == "sales unit":
                mapped = "unit_code"
            elif col_key.startswith("designation"):
                mapped = "description"
            elif "master" in col_key and ("s2" in col_key or "$" in col_key):
                mapped = "price"
            else:
                mapped = col_key

            normalized_cols[col] = mapped

        df = df.rename(columns=normalized_cols)
        df = dedupe_columns(df)

        # Limpieza de celdas multilínea
        for c in ("description","part_number","article_no","bauteil_nr","variante"):
            if c in df.columns and str(df[c].dtype) in ("object","string"):
                df[c] = df[c].map(clean_multiline_cell)

        # Heurísticas alemanas: Bauteil-Nr + Variante -> part_number
        if "bauteil_nr" in df.columns:
            if "part_number" in df.columns and "article_no" not in df.columns:
                df["article_no"] = df["part_number"]
            base = df["bauteil_nr"].fillna("").astype(str).str.strip()
            if "variante" in df.columns:
                var = df["variante"].fillna("").astype(str).str.strip()
                df["part_number"] = (base + " " + var).str.strip()
            else:
                df["part_number"] = base
            df.loc[df["part_number"].eq(""), "part_number"] = None

        # ---- detectar columnas de tiers (Qty/ea) ----
        tier_specs: List[Dict[str, Any]] = []
        tier_col_names: set = set()

        for col in df.columns:
            col_lower = str(col).strip().lower()
            if col_lower in qty_ranges_by_col_lower:
                range_text = qty_ranges_by_col_lower[col_lower]
                min_q, max_q = parse_qty_range(range_text)
                tier_specs.append({"col": col, "min_qty": min_q, "max_qty": max_q})
                tier_col_names.add(str(col))

        for col in df.columns:
            min_q, max_q = parse_tier_header(col)
            if min_q is None:
                continue
            if str(col) in tier_col_names:
                continue
            tier_specs.append({"col": col, "min_qty": min_q, "max_qty": max_q})
            tier_col_names.add(str(col))

        last_part_code: Optional[str] = None

        for _, row in df.iterrows():
            # --------- código ---------
            candidates = [
                row.get("rf_part_number"),
                row.get("part_number"),
                row.get("standard_part_number"),
                row.get("supplier_part_no"),
                row.get("article_no"),
            ]
            raw_code = None
            for c in candidates:
                if c is not None and str(c).strip() not in ("", "nan", "None"):
                    raw_code = c
                    break

            if (raw_code is None or str(raw_code).strip() == "") and last_part_code:
                raw_code = last_part_code

            if raw_code is None or str(raw_code).strip() == "":
                continue

            raw_code_str = str(raw_code).strip()
            # JEHIER (trailing code)
            jehier_original_code = None
            desc_hint_from_code = None
            if ("jehier" in sup_l or "jehier" in fn_l) and isinstance(raw_code, str):
                extracted_code, desc_hint = extract_jehier_trailing_code(raw_code_str)
                if extracted_code:
                    jehier_original_code = raw_code_str
                    raw_code_str = extracted_code
                    desc_hint_from_code = desc_hint

            part_number_full, part_number_root = normalize_part_number(raw_code_str)

            code_lower = part_number_full.lower()
            if any(x in code_lower for x in ("part number", "description", "unit price")):
                continue

            last_part_code = raw_code_str

            # --------- description ---------
            description = row.get("description")
            description = "" if description is None or str(description) == "nan" else str(description)
            if (not description.strip() or description.strip() in ("-", "—")) and desc_hint_from_code:
                description = str(desc_hint_from_code).strip()

            # --------- currency ---------
            currency = row.get("currency")
            currency = (str(currency).upper().strip() if currency is not None and str(currency) != "nan" else "")
            if currency in ("$", "US$", "USD$", "USD"):
                currency = "USD"
            elif currency in ("€", "EUR", "EURO", "EUROS"):
                currency = "EUR"
            elif currency in ("£", "GBP"):
                currency = "GBP"
            if not currency:
                currency = default_currency

            # --------- MOQ / min qty ---------
            min_qty_default = 1
            min_qty = row.get("min_qty")
            if min_qty is not None and str(min_qty) != "nan":
                s_min = str(min_qty).strip().replace(",", ".")
                m_num = re.search(r"[-+]?\d*\.?\d+", s_min)
                if m_num:
                    try:
                        f = float(m_num.group(0))
                        if f > 0 and float(f).is_integer():
                            min_qty_default = int(f)
                    except Exception:
                        pass
            moq_from_comments = parse_moq_from_text(row.get("comments"))
            if moq_from_comments and min_qty_default == 1:
                min_qty_default = moq_from_comments

            # --------- base price ---------
            base_price: Optional[float] = None
            pricing_note: Optional[str] = None
            price_candidate_cols = ["price", "price_distributor", "price_dealer", "price_retail"]
            for c_name in df.columns:
                if str(c_name).startswith("price__") and c_name not in price_candidate_cols:
                    price_candidate_cols.append(c_name)

            for c in price_candidate_cols:
                v = row.get(c)
                if v is None or str(v) == "nan":
                    continue
                p = parse_price_value(v)
                if p is not None and base_price is None:
                    base_price = p
                note = detect_pricing_note(v)
                if note and pricing_note is None:
                    pricing_note = note

            # --------- tiers ---------
            tier_prices: List[Tuple[Optional[int], Optional[int], float]] = []
            for spec in tier_specs:
                col_name = spec["col"]
                raw_val = row.get(col_name)
                if raw_val is None or str(raw_val) == "nan":
                    continue
                unit_price = parse_price_value(raw_val)
                if unit_price is None:
                    if isinstance(raw_val, str) and raw_val.strip():
                        pricing_note = pricing_note or detect_pricing_note(raw_val) or raw_val.strip()
                    continue
                min_q = spec.get("min_qty") or min_qty_default
                max_q = spec.get("max_qty")
                tier_prices.append((min_q, max_q, unit_price))

            if tier_prices and base_price is None:
                tier_prices_sorted = sorted(tier_prices, key=lambda t: t[0] if t[0] is not None else 0)
                base_price = tier_prices_sorted[0][2]

            # ---- guardar part (único) ----
            if part_number_full not in parts_rows:
                parts_rows[part_number_full] = {
                    "part_number": part_number_full,
                    "part_number_root": part_number_root,
                    "description": description.strip() or None,
                    "currency": currency,
                    "base_price": base_price,
                    "min_qty_default": min_qty_default,
                    "pricing_note": pricing_note,
                    "source_file": ctx.filename,
                    "source_sheet": sheet_name,
                }
            else:
                # completar vacíos
                if not parts_rows[part_number_full].get("description") and description.strip():
                    parts_rows[part_number_full]["description"] = description.strip()
                if parts_rows[part_number_full].get("base_price") is None and base_price is not None:
                    parts_rows[part_number_full]["base_price"] = base_price

            # ---- tiers rows ----
            for min_q, max_q, unit_price in tier_prices:
                tiers_rows.append({
                    "part_number": part_number_full,
                    "min_qty": int(min_q) if min_q is not None else min_qty_default,
                    "max_qty": int(max_q) if max_q is not None else None,
                    "unit_price": unit_price,
                    "currency": currency,
                    "source_file": ctx.filename,
                    "source_sheet": sheet_name,
                })

            # ---- aliases (si aparecen) ----
            alias_values: List[Any] = [
                row.get("standard_part_number"),
                row.get("rf_part_number"),
                row.get("supplier_part_no"),
                row.get("article_no"),
            ]
            if jehier_original_code:
                alias_values.append(jehier_original_code)

            main_norm = normalize_pn(part_number_full)
            seen: set = set()
            for raw_alias in alias_values:
                for a in extract_alias_codes(raw_alias):
                    if not a:
                        continue
                    if normalize_pn(a) == main_norm:
                        continue
                    n = normalize_pn(a)
                    if n in seen:
                        continue
                    seen.add(n)
                    alias_rows.append({
                        "part_number": part_number_full,
                        "alias_code": str(a).strip(),
                        "source": "AUTO",
                        "source_file": ctx.filename,
                        "source_sheet": sheet_name,
                    })

            # ---- attributes extra (opcional, liviano) ----
            for k, v in row.items():
                if k in ("part_number","description","currency","price","min_qty"):
                    continue
                if v is None or str(v) == "nan":
                    continue
                # guardar solo campos cortos/útiles
                if isinstance(v, str) and len(v) > 200:
                    continue
                attr_rows.append({
                    "part_number": part_number_full,
                    "key": str(k),
                    "value": str(v),
                    "source_file": ctx.filename,
                    "source_sheet": sheet_name,
                })

    parts_df = pd.DataFrame(list(parts_rows.values())) if parts_rows else pd.DataFrame()
    tiers_df = pd.DataFrame(tiers_rows) if tiers_rows else pd.DataFrame()
    aliases_df = pd.DataFrame(alias_rows) if alias_rows else pd.DataFrame()
    attrs_df = pd.DataFrame(attr_rows) if attr_rows else pd.DataFrame()

    return ParseResult(parts=parts_df, tiers=tiers_df, aliases=aliases_df, attributes=attrs_df, parser_name="xlsx_generic")
