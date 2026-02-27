from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db import get_db
from app import models

router = APIRouter(tags=["stats"])


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    suppliers = db.query(func.count(models.Supplier.id)).scalar() or 0
    catalogs = db.query(func.count(models.Catalog.id)).scalar() or 0
    parts = db.query(func.count(models.Part.id)).scalar() or 0
    tiers = db.query(func.count(models.PriceTier.id)).scalar() or 0
    return {
        "suppliers": suppliers,
        "catalogs": catalogs,
        "parts": parts,
        "price_tiers": tiers,
    }


@router.get("/stats/suppliers")
def list_suppliers(db: Session = Depends(get_db)):
    """Retorna la lista de nombres de proveedores en la BD."""
    rows = db.query(models.Supplier.name).order_by(models.Supplier.name).all()
    return [r[0] for r in rows if r[0]]
