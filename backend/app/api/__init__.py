"""REST API. All routes are mounted under /api by app.main (see docs/API.md)."""
from __future__ import annotations

from fastapi import APIRouter

from . import routes_market, routes_refresh, routes_scores

api_router = APIRouter()
api_router.include_router(routes_scores.router, tags=["scores"])
api_router.include_router(routes_market.router, tags=["market"])
api_router.include_router(routes_refresh.router, tags=["refresh"])

__all__ = ["api_router"]
