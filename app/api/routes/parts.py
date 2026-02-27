from __future__ import annotations

import io
import re
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func

from app.db import get_db
from app import models
from app.utils.search import (
    normalize_pn,
    sql_normalize,
    json_safe_number,
    smart_prefix,
    wildcard_x_match,
)

router = APIRouter(prefix="/parts", tags=["parts"])


def format_prices_for_export(part) -> str:
    tiers = list(getattr(part, "price_tiers", []) or [])
    if tiers:
        tiers.sort(key=lambda x: (x.min_qty is None, x.min_qty or 0))
        chunks = []
        for t in tiers:
            cur = t.currency or part.currency or ""
            if t.max_qty is not None:
                label = f"{t.min_qty}-{t.max_qty}"
            else:
                label = f">={t.min_qty}"
            chunks.append(f"{label}: {t.unit_price} {cur}".strip())
        return " | ".join(chunks)

    if part.base_price is not None:
        cur = part.currency or ""
        mq = part.min_qty_default or 1
        return f">={mq}: {part.base_price} {cur}".strip()

    return ""


@router.get("/currencies")
def list_currencies(db: Session = Depends(get_db)):
    """Retorna las monedas unicas presentes en la BD."""
    part_currencies = (
        db.query(models.Part.currency)
        .filter(models.Part.currency != None, models.Part.currency != "")
        .distinct()
        .all()
    )
    tier_currencies = (
        db.query(models.PriceTier.currency)
        .filter(models.PriceTier.currency != None, models.PriceTier.currency != "")
        .distinct()
        .all()
    )
    currencies = sorted(set(
        [r[0].strip().upper() for r in part_currencies if r[0]]
        + [r[0].strip().upper() for r in tier_currencies if r[0]]
    ))
    return currencies


@router.get("/search")
def search_parts(
    query: str = Query(..., alias="q", min_length=1),
    vendor: str = Query(None),
    currency: str = Query(None),
    db: Session = Depends(get_db),
):
    raw = (query or "").strip()
    if not raw:
        return []

    vendor_clean = (vendor or "").strip()
    currency_clean = (currency or "").strip().upper()

    terms = [t.strip() for t in re.split(r"[,\n;\t]+", raw) if t.strip()]

    groups = []
    for t in terms:
        t_norm = normalize_pn(t)
        like_prefix = f"{t}%"
        like_any = f"%{t}%"
        like_prefix_norm = f"{t_norm}%"
        like_any_norm = f"%{t_norm}%"

        groups.append(
            or_(
                models.Part.part_number_full.ilike(like_prefix),
                models.Part.part_number_root.ilike(like_prefix),
                models.Part.description.ilike(like_any),
                models.PartAttribute.attr_value.ilike(like_any),
                models.PartAlias.code.ilike(like_any),
                sql_normalize(models.Part.part_number_full, db).ilike(like_prefix_norm),
                sql_normalize(models.Part.part_number_root, db).ilike(like_prefix_norm),
                sql_normalize(models.PartAlias.code, db).ilike(like_any_norm),
            )
        )

    combined_filter = or_(*groups)

    q = (
        db.query(models.Part, models.Supplier)
        .join(models.Supplier, models.Part.supplier_id == models.Supplier.id)
        .outerjoin(models.PartAttribute, models.PartAttribute.part_id == models.Part.id)
        .outerjoin(models.PartAlias, models.PartAlias.part_id == models.Part.id)
        .filter(combined_filter)
    )

    if vendor_clean:
        q = q.filter(models.Supplier.name.ilike(f"%{vendor_clean}%"))

    if currency_clean:
        q = q.filter(
            or_(
                func.upper(models.Part.currency) == currency_clean,
                models.Part.id.in_(
                    db.query(models.PriceTier.part_id)
                    .filter(func.upper(models.PriceTier.currency) == currency_clean)
                    .subquery()
                )
            )
        )

    rows = q.order_by(models.Part.part_number_full).all()

    results = []
    seen_parts = set()

    def push_part(part, supplier):
        if part.id in seen_parts:
            return
        seen_parts.add(part.id)
        item = {
            "id": part.id,
            "part_number_full": part.part_number_full,
            "part_number_root": part.part_number_root,
            "description": part.description,
            "currency": part.currency,
            "base_price": json_safe_number(part.base_price),
            "min_qty_default": json_safe_number(part.min_qty_default),
            "catalog_id": part.catalog_id,
            "supplier_id": part.supplier_id,
            "supplier_name": supplier.name,
            "price_tiers": [
                {
                    "id": pt.id,
                    "min_qty": json_safe_number(pt.min_qty),
                    "max_qty": json_safe_number(pt.max_qty),
                    "unit_price": json_safe_number(pt.unit_price),
                    "currency": pt.currency,
                }
                for pt in (getattr(part, "price_tiers", None) or [])
            ],
            "attributes": [
                {
                    "attr_name": attr.attr_name,
                    "attr_value": attr.attr_value,
                }
                for attr in (getattr(part, "attributes", None) or [])
            ],
            "aliases": [
                {
                    "code": alias.code,
                    "source": alias.source,
                }
                for alias in (getattr(part, "aliases", None) or [])
            ],
        }
        results.append(item)

    for part, supplier in rows:
        push_part(part, supplier)

    return results


@router.get("/export")
def export_search_results(
    query: str = Query(..., alias="q", min_length=1),
    vendor: str = Query(None),
    currency: str = Query(None),
    db: Session = Depends(get_db),
):
    results = search_parts(query=query, vendor=vendor, currency=currency, db=db)

    records = []
    for r in results:
        records.append(
            {
                "supplier_name": r.get("supplier_name"),
                "part_number_full": r.get("part_number_full"),
                "description": r.get("description"),
                "currency": r.get("currency"),
                "base_price": r.get("base_price"),
                "min_qty_default": r.get("min_qty_default"),
                "price_tiers": " | ".join(
                    [
                        (f"{pt.get('min_qty')}-{pt.get('max_qty')}: {pt.get('unit_price')} {pt.get('currency')}")
                        if pt.get("max_qty") is not None
                        else (f">={pt.get('min_qty')}: {pt.get('unit_price')} {pt.get('currency')}")
                        for pt in (r.get("price_tiers") or [])
                    ]
                ),
            }
        )

    df = pd.DataFrame(records)
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="results")
    bio.seek(0)

    return StreamingResponse(
        bio,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=search_results.xlsx"},
    )
