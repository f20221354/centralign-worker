"""Deterministic HTTP clients for the portal and ERP JSON APIs (reads, verification, admin for demos/tests)."""
from __future__ import annotations

import os
from typing import Any

import httpx

from src.contracts import Observation, ObsStatus


def portal_url() -> str:
    return os.environ.get("PORTAL_URL", "http://127.0.0.1:8000/portal").rstrip("/")


def erp_url() -> str:
    return os.environ.get("ERP_URL", "http://127.0.0.1:8000/erp").rstrip("/")


class PortalAPI:
    def __init__(self) -> None:
        self.h = httpx.Client(base_url=portal_url(), timeout=20,
                              headers={"X-API-Key": os.environ.get("PORTAL_API_KEY", "portal-dev-key")})

    def vendors(self, q: str = "") -> list[dict[str, Any]]:
        return self.h.get("/api/vendors", params={"q": q}).raise_for_status().json()

    def invoices(self, vendor_id: str) -> list[dict[str, Any]]:
        return self.h.get(f"/api/vendors/{vendor_id}/invoices").raise_for_status().json()

    def invoice(self, vendor_id: str, number: str) -> dict[str, Any] | None:
        r = self.h.get(f"/api/vendors/{vendor_id}/invoices/{number}")
        return None if r.status_code == 404 else r.raise_for_status().json()

    def pdf(self, vendor_id: str, number: str) -> bytes:
        return self.h.get(f"/api/vendors/{vendor_id}/invoices/{number}/pdf").raise_for_status().content

    def admin(self, path: str, body: dict | None = None) -> Any:
        return self.h.post(f"/api/admin/{path}", json=body or {}).raise_for_status().json()


class ERPAPI:
    def __init__(self) -> None:
        self.h = httpx.Client(base_url=erp_url(), timeout=20,
                              headers={"X-API-Key": os.environ.get("ERP_API_KEY", "erp-dev-key")})

    def search(self, **params: str | None) -> list[dict[str, Any]]:
        return self.h.get("/api/invoices", params={k: v for k, v in params.items() if v}).raise_for_status().json()

    def get(self, erp_id: int) -> dict[str, Any] | None:
        r = self.h.get(f"/api/invoices/{erp_id}")
        return None if r.status_code == 404 else r.raise_for_status().json()

    def all_ids(self) -> list[int]:
        return [r["id"] for r in self.search()]

    def create_via_api(self, body: dict) -> httpx.Response:  # tests only: the worker writes through the browser
        return self.h.post("/api/invoices", json=body)

    def admin(self, path: str, body: dict | None = None) -> Any:
        return self.h.post(f"/api/admin/{path}", json=body or {}).raise_for_status().json()


def search_internal(erp: ERPAPI, vendor_name: str, invoice_number: str | None) -> Observation:
    """Tool: search_internal_invoices. API on purpose: a lookup should be exact, not scraped."""
    try:
        rows = erp.search(vendor_name=vendor_name, invoice_number=invoice_number)
    except httpx.HTTPStatusError as e:
        return Observation(action_name="search_internal_invoices", status=ObsStatus.HTTP_ERROR, channel="api",
                           http_status=e.response.status_code, error_message=str(e)[:200])
    except httpx.HTTPError as e:
        return Observation(action_name="search_internal_invoices", status=ObsStatus.HTTP_ERROR, channel="api",
                           error_message=f"network: {e}"[:200])
    return Observation(action_name="search_internal_invoices", status=ObsStatus.SUCCESS, channel="api",
                       data={"matches": rows, "count": len(rows)})
