from __future__ import annotations

import io
import pandas as pd

from ..registry import register
from ..types import ParseContext, ParseResult
from ...transform.result_builder import build_parse_result_from_df


def _find_header_row(df_preview: pd.DataFrame) -> int | None:
    """
    Busca la fila que contiene el encabezado real.
    Para CML suele existir 'CML Item Number' en alguna fila.
    """
    for i in range(min(len(df_preview), 40)):
        row = df_preview.iloc[i].astype(str).str.strip().str.lower()
        if row.str.contains("cml item number").any():
            return i
        # fallback por si viene distinto
        if row.str.contains("item number").any() and row.str.contains("description").any():
            return i
    return None


def parse_excel_cml(file_bytes: bytes) -> pd.DataFrame:
    """
    Parser robusto para CML (Price List y Supplement).
    - Detecta automáticamente el header real (no asume header=0)
    - Renombra PN/Description/Price si existen
    - Mantiene todas las columnas para attributes
    """
    bio = io.BytesIO(file_bytes)
    xls = pd.ExcelFile(bio)
    frames: list[pd.DataFrame] = []

    for sheet in xls.sheet_names:
        # 1) Preview sin header para buscar la fila de encabezados real
        bio.seek(0)
        preview = pd.read_excel(bio, sheet_name=sheet, header=None, nrows=60)
        if preview is None or preview.empty:
            continue

        header_row = _find_header_row(preview)

        # 2) Si encontramos header_row, releemos desde ahí
        if header_row is not None:
            bio.seek(0)
            df = pd.read_excel(bio, sheet_name=sheet, header=header_row)
        else:
            # fallback: intenta con header=0
            bio.seek(0)
            df = pd.read_excel(bio, sheet_name=sheet, header=0)

        if df is None or df.empty:
            continue

        # limpia columnas
        df.columns = [str(c).strip() for c in df.columns]

        # elimina filas completamente vacías
        df = df.dropna(how="all")

        # ---- detectar y renombrar columnas clave ----
        # PN: CML Item Number
        pn_col = None
        for c in df.columns:
            cl = c.lower()
            if "cml item number" in cl or cl in ("item number", "item no", "part number", "p/n", "pn"):
                pn_col = c
                break

        if not pn_col:
            # si esta hoja no es de items, la saltamos
            continue

        df = df.rename(columns={pn_col: "part_number"})

        # description
        for c in df.columns:
            if "description" in c.lower():
                df = df.rename(columns={c: "description"})
                break

        # price
        for c in df.columns:
            cl = c.lower()
            if "price" in cl and ("each" in cl or "unit" in cl or cl == "price"):
                df = df.rename(columns={c: "price"})
                break

        # moneda default
        if "currency" not in df.columns:
            df["currency"] = "USD"

        frames.append(df)

    if not frames:
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True)

    # filtra filas sin PN
    out["part_number"] = out["part_number"].astype(str).str.strip()
    out = out[out["part_number"].ne("") & out["part_number"].str.lower().ne("nan")]

    return out


class CMLExcelParser:
    name = "xlsx_cml"
    exts = (".xlsx",)

    def sniff(self, ctx: ParseContext, head: bytes) -> int:
        f = (ctx.filename or "").lower()
        s = 0
        if "cml" in f:
            s += 50
        if "dap" in f:
            s += 30
        if "aviation" in f:
            s += 20
        if "supplement" in f:
            s += 10
        return s

    def parse(self, file_bytes: bytes, ctx: ParseContext) -> ParseResult:
        raw = parse_excel_cml(file_bytes)

        parts, tiers, aliases, attrs = build_parse_result_from_df(
            raw,
            source_file=ctx.filename,
            default_currency="USD",
            parser_name=self.name,
        )

        return ParseResult(parts=parts, tiers=tiers, aliases=aliases, attributes=attrs, parser_name=self.name)


register(CMLExcelParser())