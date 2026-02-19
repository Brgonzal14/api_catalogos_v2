from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

def dedupe_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Asegura nombres de columnas únicos (pandas permite duplicados y rompe .get / ffill)."""
    cols = []
    counts: Dict[str, int] = {}
    for c in list(df.columns):
        base = str(c)
        n = counts.get(base, 0) + 1
        counts[base] = n
        cols.append(base if n == 1 else f"{base}__{n}")
    df.columns = cols
    return df

_tier_range_re = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")
_tier_num_re = re.compile(r"^\s*(\d+)\s*$")



def read_xlsx_fallback(xlsx_bytes: bytes) -> pd.DataFrame:
    """
    Lector de respaldo para archivos .xlsx que openpyxl no puede abrir
    (por XML inválido).
    Lee directamente xl/worksheets/sheet1.xml y xl/sharedStrings.xml.
    """
    z = zipfile.ZipFile(io.BytesIO(xlsx_bytes))
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

    # ----- sharedStrings (texto compartido) -----
    shared_strings: List[str] = []
    try:
        shared_root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in shared_root.findall("x:si", ns):
            t_el = si.find("x:t", ns)
            if t_el is not None:
                # texto simple
                shared_strings.append(t_el.text or "")
            else:
                # texto compuesto por varios <r><t>
                text = ""
                for r in si.findall("x:r", ns):
                    t = r.find("x:t", ns)
                    if t is not None and t.text:
                        text += t.text
                shared_strings.append(text)
    except KeyError:
        # libro sin sharedStrings.xml
        shared_strings = []

    # ----- sheet1.xml -----
    sheet_xml = z.read("xl/worksheets/sheet1.xml")
    root = ET.fromstring(sheet_xml)

    rows_data: List[List[Optional[str]]] = []

    for row in root.findall("x:sheetData/x:row", ns):
        row_list: List[Optional[str]] = []
        for c in row.findall("x:c", ns):
            t = c.get("t")  # tipo de celda
            v_el = c.find("x:v", ns)
            if v_el is None:
                val = None
            else:
                v = v_el.text
                if t == "s" and v is not None:
                    # índice en sharedStrings
                    idx = int(v)
                    val = shared_strings[idx] if 0 <= idx < len(shared_strings) else None
                else:
                    val = v
            row_list.append(val)
        rows_data.append(row_list)

    if not rows_data:
        return pd.DataFrame()

    # Igualamos el largo de todas las filas
    max_len = max(len(r) for r in rows_data)
    norm_rows = [r + [None] * (max_len - len(r)) for r in rows_data]

    return pd.DataFrame(norm_rows)




def detect_pricing_note(value: Any) -> Optional[str]:
    """
    Detecta textos de "nota" en celdas donde NO hay un precio numérico.
    Ejemplos típicos:
      - "On request"
      - "Call for information"
      - "Call for Price and availability"
      - "Contact us ..."
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s or s.lower() in ("nan", "none", "null", "<na>"):
        return None
    low = s.lower()

    if "on request" in low:
        return s
    if "price and leadtime" in low or "leadtime" in low and "request" in low:
        return s
    if "contact us" in low:
        return s
    if "call for" in low:
        return s
    if "call" in low and ("price" in low or "information" in low or "availabl" in low or "stock" in low):
        return s

    return None


_jehier_trailing_code_re = re.compile(r"^(?P<desc>.+?)\s+(?P<code>[A-Z]{2,}\d{2,}[A-Z0-9]+)\s*$", re.I)



def extract_alias_codes(raw: Optional[str]) -> List[str]:
    """
    Extrae códigos equivalentes desde una celda.
    Soporta:
      - separados por coma / ; / salto de línea
      - paréntesis: "CODE1 (CODE2)" -> ["CODE1", "CODE2"]
    """
    if raw is None:
        return []

    s = str(raw).strip()
    if not s or s.lower() == "nan":
        return []

    # separar por coma / ; / saltos
    chunks = [c.strip() for c in re.split(r"[,\n;]+", s) if c and c.strip()]
    out: List[str] = []

    for c in chunks:
        # CODE1 (CODE2)
        m = re.match(r"^(.*?)\((.*?)\)$", c)
        if m:
            a = m.group(1).strip()
            b = m.group(2).strip()
            if a:
                out.append(a)
            if b:
                out.append(b)
        else:
            out.append(c)

    # dedupe preservando orden
    seen = set()
    deduped = []
    for x in out:
        if x in seen:
            continue
        seen.add(x)
        deduped.append(x)

    return deduped



def extract_jehier_trailing_code(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    JEHIER: a veces el 'Part Number' viene como descripción + código al final, ej:
      'UPPER HALF HOT NOZZLE RING EME330104A'
    Devuelve (code, desc_hint). Si no matchea, (None, None).
    """
    if not text:
        return (None, None)
    s = re.sub(r"\s+", " ", str(text)).strip()
    m = _jehier_trailing_code_re.match(s)
    if not m:
        return (None, None)
    code = m.group("code").strip()
    desc = m.group("desc").strip()
    # Evitar falsos positivos: si el "desc" es muy corto, no lo usamos
    if len(desc) < 3:
        desc = None
    return (code, desc)



def detect_header_and_qty_ranges(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, str]]:
    """
    Detecta la fila de encabezados y rangos de cantidad.
    Ajusta el DataFrame para que solo contenga datos y actualiza las columnas.

    Soporta:
      - Encabezados típicos en inglés (Part Number / Description / Item)
      - Encabezados en alemán (P/N, Bauteil-Nr., Bezeichnung, Variante, Länge, Menge, VK, EUR/Unit)
    """
    header_row_idx = None

    def _has_any(vals: List[str], needles: Tuple[str, ...]) -> bool:
        for v in vals:
            for n in needles:
                if n in v:
                    return True
        return False

    part_markers = (
        "part number", "part no", "part-no", "part_number", "p/n", "pn",
        # IPECO (Excel/PDF) usa "Material" como identificador del part number
        "material", "material number", "material no", "material #",
        # Otros proveedores
        "pnr",
        # CML / Torrington
        "item number",
        "item",
        "mpn",
        "art.-nr", "art.nr", "bauteil", "bauteil-nr",
    )
    desc_markers = ("description", "bezeichnung", "desc", "designation", "désignation")
    other_markers = ("variante", "länge", "lange", "menge", "eur/unit", "vk", "qty/ea", "item", "uom", "unit of measure", "sales unit", "currency", "minimum quantity", "minimum order quantity", "moq", "lead time", "leadtime", "unit price", "price per unit", "unit eur", "unit usd", "dealer price", "retail price")

    # Buscamos en las primeras filas porque muchos catálogos traen un título arriba
    for i, row in df.head(60).iterrows():
        lower_vals = []
        for v in row.values:
            if isinstance(v, str):
                s = v.replace("\xa0", " ").strip().lower()
                if s:
                    lower_vals.append(s)

        has_part = _has_any(lower_vals, part_markers)
        has_desc = _has_any(lower_vals, desc_markers)
        hits_other = sum(1 for v in lower_vals if _has_any([v], other_markers))

        # Heurística: part + (desc o 2+ marcadores extra) => header
        if has_part and (has_desc or hits_other >= 1):
            header_row_idx = i
            break

    qty_ranges_by_col_lower: Dict[str, str] = {}
    start_data_idx = 0
    col_names: List[str] = []

    if header_row_idx is not None:
        header_row = df.iloc[header_row_idx]
        next_row = df.iloc[header_row_idx + 1] if header_row_idx + 1 < len(df) else None

        for idx, val in enumerate(header_row):
            # Normalizar encabezados numéricos (ej: 500.0 -> 500)
            if val is not None and isinstance(val, (int, float)) and not pd.isna(val):
                if float(val).is_integer():
                    raw_name = str(int(val))
                else:
                    raw_name = str(val).strip()
            else:
                raw_name = str(val).replace("\xa0", " ").strip() if val is not None else ""
            if not raw_name:
                raw_name = f"col_{idx}"

            col_name = raw_name
            col_key_lower = raw_name.strip().lower()

            # Para columnas Qty/ea, añadimos el rango de la fila siguiente
            if next_row is not None and "qty/ea" in col_key_lower:
                vr = next_row.iloc[idx]
                if isinstance(vr, str) and vr.strip():
                    range_text = vr.strip()
                    col_name = f"{raw_name} {range_text}"  # p.ej. "Qty/ea 25-99"
                    col_key_lower = col_name.strip().lower()
                    qty_ranges_by_col_lower[col_key_lower] = range_text

            col_names.append(col_name)

        # Si detectamos rangos, los datos empiezan 2 filas después;
        # si no, justo debajo de la cabecera.
        start_data_idx = header_row_idx + 1
        if qty_ranges_by_col_lower and next_row is not None:
            start_data_idx = header_row_idx + 2

        df = df.iloc[start_data_idx:].reset_index(drop=True)
        df.columns = col_names

    return df, qty_ranges_by_col_lower


