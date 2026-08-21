"""Document management routes — upload, download, list, delete."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from unified_trading_library import (
    AuthContext,
    UnifiedCloudConfig,
    create_api_auth,
    generate_download_url,
    generate_upload_url,
)

from client_reporting_api.core.entitlement import enforce_entitlement, require_internal
from client_reporting_api.mock_state import get_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

_cloud_cfg = UnifiedCloudConfig()
_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]

_DOCUMENTS_BUCKET = "client-reporting-documents"


# --- Mock data ---

MOCK_DOCUMENTS: list[dict[str, str | int | float | bool]] = [
    {
        "document_id": "DOC-001",
        "org_id": "org-alpha",
        "category": "INVOICE",
        "filename": "invoice-2026-02.pdf",
        "content_type": "application/pdf",
        "size_bytes": 245_120,
        "status": "active",
        "uploaded_at": "2026-03-01T10:00:00Z",
        "uploaded_by": "user-admin",
    },
    {
        "document_id": "DOC-002",
        "org_id": "org-alpha",
        "category": "REPORT",
        "filename": "monthly-performance-2026-02.pdf",
        "content_type": "application/pdf",
        "size_bytes": 512_000,
        "status": "active",
        "uploaded_at": "2026-03-02T14:30:00Z",
        "uploaded_by": "system",
    },
    {
        "document_id": "DOC-003",
        "org_id": "org-beta",
        "category": "CONTRACT",
        "filename": "ima-agreement-v2.pdf",
        "content_type": "application/pdf",
        "size_bytes": 1_024_000,
        "status": "active",
        "uploaded_at": "2026-02-15T09:00:00Z",
        "uploaded_by": "user-admin",
    },
    {
        "document_id": "DOC-004",
        "org_id": "org-alpha",
        "category": "COMPLIANCE",
        "filename": "mifid-report-q4-2025.pdf",
        "content_type": "application/pdf",
        "size_bytes": 780_000,
        "status": "active",
        "uploaded_at": "2026-01-10T08:00:00Z",
        "uploaded_by": "compliance-bot",
    },
]


# --- Request/Response models ---


class UploadUrlRequest(
    BaseModel,
):  # CORRECT-LOCAL: FastAPI route schema — not a shared domain contract
    org_id: str
    filename: str
    content_type: str = "application/pdf"
    category: str = "GENERAL"


class UploadUrlResponse(
    BaseModel,
):  # CORRECT-LOCAL: FastAPI route schema — not a shared domain contract
    document_id: str
    upload_url: str
    expires_in_minutes: int


class DownloadUrlResponse(
    BaseModel,
):  # CORRECT-LOCAL: FastAPI route schema — not a shared domain contract
    document_id: str
    download_url: str
    expires_in_minutes: int


# --- Seed mock store ---

_store = get_store()
for _doc in MOCK_DOCUMENTS:
    _existing = _store.get("documents", str(_doc["document_id"]))
    if _existing is None:
        _store.create("documents", {**_doc, "id": _doc["document_id"]})


# --- Routes ---


@router.post("/upload-url")
def create_upload_url(request: UploadUrlRequest, auth: AuthDep) -> dict[str, object]:
    """Return a pre-signed upload URL and allocate a document_id.

    In mock mode: returns a synthetic URL and stores document metadata in-memory.
    In live mode: generates a cloud storage pre-signed upload URL via UCI.

    Entitlement: an external caller may only upload documents attributed
    to their own org (``request.org_id`` must equal ``auth.org_id``);
    internal callers may upload for any org. 2026-08-21 CTO handoff P1
    fix.
    """
    enforce_entitlement(auth, request.org_id)
    doc_id = f"DOC-{uuid.uuid4().hex[:8].upper()}"
    object_path = f"documents/{request.org_id}/{request.category}/{doc_id}/{request.filename}"

    if _cloud_cfg.is_mock_mode():
        doc_record: dict[str, object] = {
            "id": doc_id,
            "document_id": doc_id,
            "org_id": request.org_id,
            "category": request.category,
            "filename": request.filename,
            "content_type": request.content_type,
            "size_bytes": 0,
            "status": "pending_upload",
            "uploaded_at": "2026-03-21T00:00:00Z",
            "uploaded_by": "mock-user",
        }
        _store.create("documents", doc_record)
        return {
            "document_id": doc_id,
            "upload_url": f"https://mock-storage.example.com/{_DOCUMENTS_BUCKET}/{object_path}?X-Mock-Signature=abc123",
            "expires_in_minutes": 15,
        }

    logger.info("create_upload_url: org_id=%s filename=%s", request.org_id, request.filename)
    upload_url = generate_upload_url(
        bucket=_DOCUMENTS_BUCKET,
        object_path=object_path,
        content_type=request.content_type,
        expiry_minutes=15,
    )
    return {
        "document_id": doc_id,
        "upload_url": upload_url,
        "expires_in_minutes": 15,
    }


@router.get("/{document_id}/download-url")
def get_download_url(document_id: str, auth: AuthDep) -> dict[str, object]:
    """Return a pre-signed download URL for a document.

    In mock mode: returns a synthetic URL.
    In live mode: generates a cloud storage pre-signed download URL via UCI.

    Entitlement: scoped to the document's real owning org when that's
    resolvable from the shared document store (mock-mode seed/upload
    data today). Live-mode uploads aren't persisted to any per-document
    ownership record yet (``create_upload_url``'s live branch never
    writes one), so when the owning org can't be resolved this fails
    closed to internal callers only rather than trusting a
    caller-supplied claim. 2026-08-21 CTO handoff P1 fix, judgment call
    documented in walkthrough_feedback_remediation_2026_08_21.md.
    """
    doc = _store.get("documents", document_id)
    if doc is not None:
        enforce_entitlement(auth, str(doc.get("org_id", "")))
    else:
        require_internal(auth)

    if _cloud_cfg.is_mock_mode():
        if doc is None:
            raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
        return {
            "document_id": document_id,
            "download_url": f"https://mock-storage.example.com/{_DOCUMENTS_BUCKET}/documents/{document_id}?X-Mock-Signature=def456",
            "expires_in_minutes": 60,
        }

    logger.info("get_download_url: document_id=%s", document_id)
    # In live mode the object_path would be resolved from document metadata in the database
    object_path = f"documents/{document_id}"
    download_url = generate_download_url(
        bucket=_DOCUMENTS_BUCKET,
        object_path=object_path,
        expiry_minutes=60,
    )
    return {
        "document_id": document_id,
        "download_url": download_url,
        "expires_in_minutes": 60,
    }


@router.get("")
def list_documents(
    auth: AuthDep,
    org_id: str = Query(..., description="Organisation identifier"),
    category: str | None = Query(None, description="Filter by category (INVOICE, REPORT, CONTRACT, COMPLIANCE)"),
) -> list[dict[str, object]]:
    """List documents for an organisation, optionally filtered by category.

    In mock mode: returns seed + mutated documents from the in-memory store.
    In live mode: queries document metadata from cloud storage/database.
    """
    enforce_entitlement(auth, org_id)
    if _cloud_cfg.is_mock_mode():
        all_docs = _store.list("documents")
        filtered = [d for d in all_docs if d.get("org_id") == org_id]
        if category is not None:
            filtered = [d for d in filtered if d.get("category") == category]
        return filtered

    logger.info("list_documents: org_id=%s category=%s", org_id, category)
    # Live implementation: query document metadata store
    return []


@router.delete("/{document_id}")
def delete_document(document_id: str, auth: AuthDep) -> dict[str, str]:
    """Soft-delete a document (admin only).

    In mock mode: marks the document as deleted in the in-memory store.
    In live mode: sets status=deleted in the document metadata store.

    Entitlement: internal-only, matching the docstring's pre-existing
    "(admin only)" intent — this is a destructive operation, not a
    per-client read, so it doesn't get the entitlement-by-ownership
    treatment ``get_download_url`` does. 2026-08-21 CTO handoff P1 fix.
    """
    require_internal(auth)
    if _cloud_cfg.is_mock_mode():
        doc = _store.get("documents", document_id)
        if doc is None:
            raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
        _store.update("documents", document_id, {**doc, "status": "deleted"})
        return {"document_id": document_id, "status": "deleted"}

    logger.info("delete_document: document_id=%s", document_id)
    # Live implementation: soft-delete in document metadata store
    return {"document_id": document_id, "status": "deleted"}
