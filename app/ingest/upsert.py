from __future__ import annotations

from typing import Dict, Tuple

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
    """Inserta todo como nuevos registros (por catálogo).

    Esta versión v2 es simple y estable:
    - Crea una fila Part por cada row en parts
    - Vincula tiers/aliases/attributes por part_number

    Espera (mínimo) en parts:
      part_number, description, currency, price, min_qty (opcional)
    tiers:
      part_number, min_qty, max_qty, unit_price, currency
    aliases:
      part_number, code, source
    attributes:
      part_number, attr_name, attr_value
    """

    parts = parts.copy() if parts is not None else pd.DataFrame()
    tiers = tiers.copy() if tiers is not None else pd.DataFrame()
    aliases = aliases.copy() if aliases is not None else pd.DataFrame()
    attributes = attributes.copy() if attributes is not None else pd.DataFrame()

    # normalizar columnas a lower
    for df in (parts, tiers, aliases, attributes):
        if df is not None and not df.empty:
            df.columns = [str(c).strip() for c in df.columns]

    # mapa PN -> Part.id
    pn_to_part: Dict[str, models.Part] = {}

    inserted_parts = 0
    inserted_tiers = 0
    inserted_aliases = 0
    inserted_attrs = 0

    if parts is not None and not parts.empty:
        for _, row in parts.iterrows():
            pn = str(row.get("part_number", "") or "").strip()
            if not pn:
                continue

            full, root = normalize_part_number(pn)
            part = models.Part(
                catalog_id=catalog.id,
                supplier_id=supplier.id,
                part_number_full=full,
                part_number_root=root,
                description=(row.get("description") if pd.notna(row.get("description")) else None),
                currency=(row.get("currency") if pd.notna(row.get("currency")) else None),
                base_price=(float(row.get("price")) if pd.notna(row.get("price")) else None),
                min_qty_default=(int(row.get("min_qty")) if pd.notna(row.get("min_qty")) else 1),
                is_active=True,
            )
            db.add(part)
            pn_to_part[pn] = part
            inserted_parts += 1

        db.commit()
        # refresh ids
        for pn, part in pn_to_part.items():
            db.refresh(part)

    # tiers
    if tiers is not None and not tiers.empty:
        for _, row in tiers.iterrows():
            pn = str(row.get("part_number", "") or "").strip()
            if not pn:
                continue
            part = pn_to_part.get(pn)
            if not part:
                # si no está (por ejemplo, tiers parseados sin parts), lo ignoramos
                continue
            pt = models.PriceTier(
                part_id=part.id,
                min_qty=(int(row.get("min_qty")) if pd.notna(row.get("min_qty")) else 1),
                max_qty=(int(row.get("max_qty")) if pd.notna(row.get("max_qty")) else None),
                unit_price=(float(row.get("unit_price")) if pd.notna(row.get("unit_price")) else None),
                currency=(row.get("currency") if pd.notna(row.get("currency")) else part.currency),
            )
            db.add(pt)
            inserted_tiers += 1
        db.commit()

    # aliases
    if aliases is not None and not aliases.empty:
        for _, row in aliases.iterrows():
            pn = str(row.get("part_number", "") or "").strip()
            code = str(row.get("code", "") or "").strip()
            if not pn or not code:
                continue
            part = pn_to_part.get(pn)
            if not part:
                continue
            al = models.PartAlias(
                part_id=part.id,
                code=code,
                source=(row.get("source") if pd.notna(row.get("source")) else None),
            )
            db.add(al)
            inserted_aliases += 1
        db.commit()

    # attributes
    if attributes is not None and not attributes.empty:
        for _, row in attributes.iterrows():
            pn = str(row.get("part_number", "") or "").strip()
            if not pn:
                continue
            part = pn_to_part.get(pn)
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

    return {
        "parts": inserted_parts,
        "tiers": inserted_tiers,
        "aliases": inserted_aliases,
        "attributes": inserted_attrs,
    }
