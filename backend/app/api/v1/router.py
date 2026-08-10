"""Agregador dos routers da API v1."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    accounts,
    admin,
    auth,
    catalog,
    costs,
    dashboard,
    finance,
    live,
    oauth,
    operations,
    orders,
    reports,
    webhooks,
)

api_router = APIRouter()

# Públicos (sem JWT): autenticação e recepção de webhooks.
api_router.include_router(auth.router)
api_router.include_router(webhooks.router)

# Autenticados.
api_router.include_router(oauth.router)
api_router.include_router(accounts.router)
api_router.include_router(live.router)
api_router.include_router(dashboard.router)
api_router.include_router(orders.router)
api_router.include_router(finance.router)
api_router.include_router(catalog.router)
api_router.include_router(costs.router)
api_router.include_router(operations.router)
api_router.include_router(reports.router)
api_router.include_router(admin.router)
