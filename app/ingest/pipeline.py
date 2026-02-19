from __future__ import annotations

from typing import Dict, Any

from sqlalchemy.orm import Session

from app.parsers.dispatch import parse_file
from app.ingest.upsert import get_or_create_supplier, create_catalog, upsert_parse_result


def ingest_catalog(
    db: Session,
    *,
    file_bytes: bytes,
    filename: str,
    supplier_name: str,
    year: int,
) -> Dict[str, Any]:
    """Pipeline estable: detect -> parse (ParseResult) -> insert.

    Parsers devuelven DataFrames estándar (Option A). Aquí los insertamos en DB.
    """

    supplier = get_or_create_supplier(db, supplier_name)
    catalog = create_catalog(db, supplier, year, filename)

    result = parse_file(file_bytes, filename=filename, supplier_name=supplier_name)

    counts = upsert_parse_result(
        db,
        catalog=catalog,
        supplier=supplier,
        parts=result.parts,
        tiers=result.tiers,
        aliases=result.aliases,
        attributes=result.attributes,
    )

    return {
        "catalog_id": catalog.id,
        "supplier_id": supplier.id,
        "parser": result.parser_name,
        "counts": counts,
    }
