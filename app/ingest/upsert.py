from __future__ import annotations

from typing import Dict, Iterable, List, Any

import pandas as pd
from sqlalchemy.orm import Session

from app import models
from app.utils.search import normalize_part_number


def get_or_create_supplier(db: Session, name: str) -> models.Supplier:
    supplier = db.query(models.Supplier).filter(models.Supplier.name == name).first()
    if supplier:
        return supplier
    supplier = models.Supplier(name=name)
    db.add(supplier)
    db.commit()
    db.refresh(supplier)
    return supplier


def create_catalog(db: Session, supplier: models.Supplier, year: int, filename: str) -> models.Catalog:
    catalog = models.Catalog(supplier_id=supplier.id, year=year, original_filename=filename)
    db.add(catalog)
    db.commit()
    db.refresh(catalog)
    return catalog


def _notna(v) -> bool:
    try:
        return pd.notna(v)
    except Exception:
        return v is not None


def _to_int(v, default=None):
    if not _notna(v):
        return default
    s = str(v).strip()
    if not s:
        return default
    try:
        return int(float(s))
    except Exception:
        return default


def _to_float(v, default=None):
    if not _notna(v):
        return default
    s = str(v).strip()
    if not s:
        return default
    # soporta "1.234,56" y "1,234.56"
    s = s.replace(" ", "")
    if "," in s and "." in s:
        # si la coma parece decimal (va después del punto), cambiamos formato europeo a estándar
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    else:
        # si solo hay coma, la tomamos como decimal
        if "," in s and "." not in s:
            s = s.replace(",", ".")
    try:
        return float(s)
    except Exception:
        return default


def _clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _rename_if_present(df: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    cols = {c: mapping[c] for c in df.columns if c in mapping}
    return df.rename(columns=cols) if cols else df


def _iter_alias_codes(v: Any) -> Iterable[str]:
    """
    Acepta:
    - lista ['A','B']
    - string "A, B\nC"
    - None
    Devuelve códigos limpios.
    """
    if not _notna(v) or v is None:
        return []
    if isinstance(v, list) or isinstance(v, tuple) or isinstance(v, set):
        for x in v:
            s = str(x).strip()
            if s:
                yield s
        return
    # string
    s = str(v).strip()
    if not s:
        return
    # split por coma o salto de línea
    for token in s.replace("\r", "\n").split("\n"):
        for t in token.split(","):
            tt = t.strip()
            if tt:
                yield tt


def upsert_parse_result(
    db: Session,
    *,
    catalog: models.Catalog,
    supplier: models.Supplier,
    parts: pd.DataFrame,
    tiers: pd.DataFrame,
    aliases: pd.DataFrame,
    attributes: pd.DataFrame,
) -> Dict[str, int]:
    """
    Inserta todo como nuevos registros (por catálogo), tolerante a distintas variantes de nombres.

    - Inserta Parts desde `parts`
    - Vincula tiers/aliases/attributes por part_number (raw del DF)
    """

    parts = parts.copy() if parts is not None else pd.DataFrame()
    tiers = tiers.copy() if tiers is not None else pd.DataFrame()
    aliases = aliases.copy() if aliases is not None else pd.DataFrame()
    attributes = attributes.copy() if attributes is not None else pd.DataFrame()

    parts = _clean_columns(parts)
    tiers = _clean_columns(tiers)
    aliases = _clean_columns(aliases)
    attributes = _clean_columns(attributes)

    # Compat: normaliza nombres a los esperados por el upsert
    parts = _rename_if_present(
        parts,
        {
            "part_number_full": "part_number",
            "pn": "part_number",
            "code": "part_number",
            "desc": "description",
            "base_price": "price",
            "unit_price": "price",
            "moq": "min_qty",
            "min_qty_default": "min_qty",
        },
    )

    tiers = _rename_if_present(
        tiers,
        {
            "part_number_full": "part_number",
            "pn": "part_number",
            "min": "min_qty",
            "max": "max_qty",
            "price": "unit_price",
        },
    )

    aliases = _rename_if_present(
        aliases,
        {
            "part_number_full": "part_number",
            "pn": "part_number",
            "alias_code": "code",
        },
    )

    attributes = _rename_if_present(
        attributes,
        {
            "part_number_full": "part_number",
            "pn": "part_number",
            "key": "attr_name",
            "value": "attr_value",
        },
    )

    # ✅ NUEVO: si parts trae columna "aliases" y aliases DF viene vacío,
    #          construimos aliases DF (para buscar también por END-UNIT)
    if (aliases is None or aliases.empty) and (parts is not None and not parts.empty) and ("aliases" in parts.columns):
        rows: List[dict] = []
        for _, r in parts.iterrows():
            raw_pn = str(r.get("part_number", "") or "").strip()
            if not raw_pn:
                continue
            for code in _iter_alias_codes(r.get("aliases")):
                rows.append({"part_number": raw_pn, "code": code, "source": "end_unit"})
        if rows:
            aliases = pd.DataFrame(rows)

    # mapa PN(raw del DF) -> Part(obj)
    pn_to_part: Dict[str, models.Part] = {}

    inserted_parts = 0
    inserted_tiers = 0
    inserted_aliases = 0
    inserted_attrs = 0

    pending_extra_attrs = []  # (raw_pn, key, value)

    # PARTS
    if parts is not None and not parts.empty:
        for _, row in parts.iterrows():
            raw_pn = str(row.get("part_number", "") or "").strip()
            if not raw_pn:
                continue

            full, root = normalize_part_number(raw_pn)

            desc = row.get("description")
            cur = row.get("currency")
            price = row.get("price")
            min_qty = row.get("min_qty")

            part = models.Part(
                catalog_id=catalog.id,
                supplier_id=supplier.id,
                part_number_full=full,
                part_number_root=root,
                description=(str(desc).strip() if _notna(desc) and str(desc).strip() else None),
                currency=(str(cur).strip() if _notna(cur) and str(cur).strip() else None),
                base_price=_to_float(price, default=None),
                min_qty_default=_to_int(min_qty, default=1) or 1,
                is_active=True,
            )
            db.add(part)
            pn_to_part[raw_pn] = part
            inserted_parts += 1

            # Guardar columnas extra como attributes
            core_cols = {
                "part_number",
                "description",
                "currency",
                "price",
                "min_qty",
                "source_file",
                "parser_name",
                # ✅ no guardamos aliases como attribute (ya lo insertamos como PartAlias)
                "aliases",
            }
            for col in parts.columns:
                if col in core_cols:
                    continue
                v = row.get(col)
                if not _notna(v):
                    continue
                sv = str(v).strip()
                if not sv:
                    continue
                pending_extra_attrs.append((raw_pn, col, sv))

        db.commit()
        for _, part in pn_to_part.items():
            db.refresh(part)

    # TIERS
    if tiers is not None and not tiers.empty:
        for _, row in tiers.iterrows():
            raw_pn = str(row.get("part_number", "") or "").strip()
            if not raw_pn:
                continue
            part = pn_to_part.get(raw_pn)
            if not part:
                continue

            min_q = _to_int(row.get("min_qty"), default=1) or 1
            max_q = _to_int(row.get("max_qty"), default=None)
            unit_p = _to_float(row.get("unit_price"), default=None)
            cur = row.get("currency")
            cur = (str(cur).strip() if _notna(cur) and str(cur).strip() else None) or part.currency

            pt = models.PriceTier(
                part_id=part.id,
                min_qty=min_q,
                max_qty=max_q,
                unit_price=unit_p,
                currency=cur,
            )
            db.add(pt)
            inserted_tiers += 1
        db.commit()

    # ALIASES
    if aliases is not None and not aliases.empty:
        for _, row in aliases.iterrows():
            raw_pn = str(row.get("part_number", "") or "").strip()
            code = str(row.get("code", "") or "").strip()
            if not raw_pn or not code:
                continue
            part = pn_to_part.get(raw_pn)
            if not part:
                continue

            src = row.get("source")
            al = models.PartAlias(
                part_id=part.id,
                code=code,
                source=(str(src).strip() if _notna(src) and str(src).strip() else None),
            )
            db.add(al)
            inserted_aliases += 1
        db.commit()

    # ATTRIBUTES (desde DF attributes)
    if attributes is not None and not attributes.empty:
        for _, row in attributes.iterrows():
            raw_pn = str(row.get("part_number", "") or "").strip()
            if not raw_pn:
                continue
            part = pn_to_part.get(raw_pn)
            if not part:
                continue

            name = str(row.get("attr_name", "") or "").strip()
            val = str(row.get("attr_value", "") or "").strip()
            if not name or not val:
                continue

            at = models.PartAttribute(part_id=part.id, attr_name=name, attr_value=val)
            db.add(at)
            inserted_attrs += 1
        db.commit()

    # ATTRIBUTES extra (desde columnas no-core en parts)
    if pending_extra_attrs:
        for raw_pn, key, val in pending_extra_attrs:
            part = pn_to_part.get(raw_pn)
            if not part:
                continue
            at = models.PartAttribute(part_id=part.id, attr_name=str(key), attr_value=str(val))
            db.add(at)
            inserted_attrs += 1
        db.commit()

    return {
        "parts": inserted_parts,
        "tiers": inserted_tiers,
        "aliases": inserted_aliases,
        "attributes": inserted_attrs,
    }