from __future__ import annotations

import os
from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app import models, schemas
from app.ingest.pipeline import ingest_catalog

router = APIRouter(prefix="/catalogs", tags=["catalogs"])


@router.post("/upload")
async def upload_catalog(
    supplier_name: str = Form(...),
    year: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Archivo vacío")

    info = ingest_catalog(
        db,
        file_bytes=content,
        filename=file.filename or "",
        supplier_name=supplier_name,
        year=year,
    )

    return {
        "message": "Catálogo cargado correctamente",
        **info,
    }


@router.get("", response_model=list[schemas.CatalogListOut])
def list_catalogs(db: Session = Depends(get_db)):
    rows = (
        db.query(models.Catalog, models.Supplier)
        .join(models.Supplier, models.Catalog.supplier_id == models.Supplier.id)
        .order_by(models.Catalog.created_at.desc())
        .all()
    )
    out: list[schemas.CatalogListOut] = []
    for catalog, supplier in rows:
        out.append(
            schemas.CatalogListOut(
                id=catalog.id,
                supplier_name=supplier.name,
                year=catalog.year,
                original_filename=catalog.original_filename,
                created_at=catalog.created_at,
            )
        )
    return out


@router.delete("/{catalog_id}")
def delete_catalog(catalog_id: int, db: Session = Depends(get_db)):
    catalog = db.query(models.Catalog).filter(models.Catalog.id == catalog_id).first()
    if not catalog:
        raise HTTPException(status_code=404, detail="Catálogo no encontrado")

    # cascade manual: Part -> tiers/attrs/aliases
    parts = db.query(models.Part).filter(models.Part.catalog_id == catalog_id).all()
    part_ids = [p.id for p in parts]
    if part_ids:
        db.query(models.PriceTier).filter(models.PriceTier.part_id.in_(part_ids)).delete(synchronize_session=False)
        db.query(models.PartAttribute).filter(models.PartAttribute.part_id.in_(part_ids)).delete(synchronize_session=False)
        db.query(models.PartAlias).filter(models.PartAlias.part_id.in_(part_ids)).delete(synchronize_session=False)
        db.query(models.Part).filter(models.Part.id.in_(part_ids)).delete(synchronize_session=False)

    db.delete(catalog)
    db.commit()
    return {"message": "Catálogo eliminado"}


@router.delete("")
def delete_all_catalogs(db: Session = Depends(get_db)):
    # orden: tiers/attrs/aliases -> parts -> catalogs
    db.query(models.PriceTier).delete(synchronize_session=False)
    db.query(models.PartAttribute).delete(synchronize_session=False)
    db.query(models.PartAlias).delete(synchronize_session=False)
    db.query(models.Part).delete(synchronize_session=False)
    db.query(models.Catalog).delete(synchronize_session=False)
    db.commit()
    return {"message": "Todos los catálogos eliminados"}


@router.get("/export/standard.xlsx")
def export_standard_template():
    # Mantener endpoint que usa el front para descargar el template
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    xlsx_path = os.path.join(base_dir, "estandar.xlsx")
    if not os.path.exists(xlsx_path):
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")

    from fastapi.responses import FileResponse

    return FileResponse(
        xlsx_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="standard.xlsx",
    )
