from .catalogs import router as catalogs_router
from .parts import router as parts_router
from .stats import router as stats_router

__all__ = ["catalogs_router", "parts_router", "stats_router"]
