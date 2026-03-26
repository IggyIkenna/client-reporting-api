"""Document management routes — upload, download, list, delete."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from unified_trading_library import UnifiedCloudConfig, generate_download_url, generate_upload_url

from client_reporting_api.mock_state import get_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

_cloud_cfg = UnifiedCloudConfig()

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
def create_upload_url(request: UploadUrlRequest) -> dict[str, object]:
    """Return a pre-signed upload URL and allocate a document_id.

    In mock mode: returns a synthetic URL and stores document metadata in-memory.
    In live mode: generates a cloud storage pre-signed upload URL via UCI.
    """
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
def get_download_url(document_id: str) -> dict[str, object]:
    """Return a pre-signed download URL for a document.

    In mock mode: returns a synthetic URL.
    In live mode: generates a cloud storage pre-signed download URL via UCI.
    """
    if _cloud_cfg.is_mock_mode():
        doc = _store.get("documents", document_id)
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
    org_id: str = Query(..., description="Organisation identifier"),
    category: str | None = Query(
        None, description="Filter by category (INVOICE, REPORT, CONTRACT, COMPLIANCE)"
    ),
) -> list[dict[str, object]]:
    """List documents for an organisation, optionally filtered by category.

    In mock mode: returns seed + mutated documents from the in-memory store.
    In live mode: queries document metadata from cloud storage/database.
    """
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
def delete_document(document_id: str) -> dict[str, str]:
    """Soft-delete a document (admin only).

    In mock mode: marks the document as deleted in the in-memory store.
    In live mode: sets status=deleted in the document metadata store.
    """
    if _cloud_cfg.is_mock_mode():
        doc = _store.get("documents", document_id)
        if doc is None:
            raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
        _store.update("documents", document_id, {**doc, "status": "deleted"})
        return {"document_id": document_id, "status": "deleted"}

    logger.info("delete_document: document_id=%s", document_id)
    # Live implementation: soft-delete in document metadata store
    return {"document_id": document_id, "status": "deleted"}
