from __future__ import annotations

import io
import re
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

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


@router.get("/search")
def search_parts(
    query: str = Query(..., alias="q", min_length=1, description="Código o descripción (puede ser múltiple separado por coma o líneas)"),
    vendor: str = Query(None, description="Proveedor (opcional). Filtra por nombre del supplier."),
    db: Session = Depends(get_db),
):
    raw = (query or "").strip()
    if not raw:
        return []

    vendor_clean = (vendor or "").strip()

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
                for pt in getattr(part, "price_tiers", [])
            ],
            "aliases": [
                {"id": al.id, "code": al.code, "source": al.source}
                for al in db.query(models.PartAlias).filter(models.PartAlias.part_id == part.id).all()
            ],
            "attributes": [
                {"id": attr.id, "attr_name": attr.attr_name, "attr_value": attr.attr_value}
                for attr in getattr(part, "attributes", [])
            ],
        }
        results.append(item)

    MAX_RESULTS = 20000
    MAX_WILDCARD_CANDIDATES = 20000

    for part, supplier in rows:
        push_part(part, supplier)
        if len(results) >= MAX_RESULTS:
            return results

    # wildcard X segunda pasada
    if len(results) < MAX_RESULTS:
        for t in terms:
            t_clean = (t or "").strip()
            if not t_clean:
                continue
            t_norm = normalize_pn(t_clean)

            pref = smart_prefix(t_norm, min_len=6)
            if not pref:
                continue

            q2 = (
                db.query(models.Part, models.Supplier)
                .join(models.Supplier, models.Part.supplier_id == models.Supplier.id)
                .outerjoin(models.PartAlias, models.PartAlias.part_id == models.Part.id)
                .filter(
                    or_(
                        and_(
                            sql_normalize(models.Part.part_number_full, db).ilike(f"{pref}%"),
                            sql_normalize(models.Part.part_number_full, db).ilike("%X%"),
                        ),
                        and_(
                            sql_normalize(models.PartAlias.code, db).ilike(f"{pref}%"),
                            sql_normalize(models.PartAlias.code, db).ilike("%X%"),
                        ),
                    )
                )
            )

            if vendor_clean:
                q2 = q2.filter(models.Supplier.name.ilike(f"%{vendor_clean}%"))

            wildcard_candidates = q2.limit(MAX_WILDCARD_CANDIDATES).all()

            for part, supplier in wildcard_candidates:
                if part.id in seen_parts:
                    continue

                ok = wildcard_x_match(part.part_number_full, t_clean)

                if not ok:
                    alias_rows = (
                        db.query(models.PartAlias.code)
                        .filter(models.PartAlias.part_id == part.id)
                        .all()
                    )
                    ok = any(wildcard_x_match(code, t_clean) for (code,) in alias_rows if code)

                if ok:
                    push_part(part, supplier)
                    if len(results) >= MAX_RESULTS:
                        break

            if len(results) >= MAX_RESULTS:
                break

    return results


@router.get("/search/export")
def export_search_results(
    query: str = Query(..., alias="q", min_length=1),
    vendor: str = Query(None),
    db: Session = Depends(get_db),
):
    rows = search_parts(query=query, vendor=vendor, db=db)

    # convertir a DF
    records = []
    for r in rows:
        records.append(
            {
                "part_number": r.get("part_number_full"),
                "description": r.get("description"),
                "supplier": r.get("supplier_name"),
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
    filename = "search_results.xlsx"
    return StreamingResponse(
        bio,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
