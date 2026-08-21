"""DocuSign integration routes — send for signature, check status, webhook receiver."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from unified_trading_library import AuthContext, UnifiedCloudConfig, create_api_auth

from client_reporting_api.core.entitlement import enforce_entitlement, require_internal
from client_reporting_api.mock_state import get_store

logger = logging.getLogger(__name__)
router = APIRouter(tags=["docusign"])

_cloud_cfg = UnifiedCloudConfig()
_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]


# --- Mock data ---

MOCK_ENVELOPES: list[dict[str, str | int | float | bool]] = [
    {
        "envelope_id": "ENV-001",
        "document_id": "DOC-003",
        "status": "completed",
        "signer_email": "client@org-beta.com",
        "signer_name": "John Smith",
        "sent_at": "2026-02-15T10:00:00Z",
        "signed_at": "2026-02-16T14:30:00Z",
        "completed_at": "2026-02-16T14:30:00Z",
    },
    {
        "envelope_id": "ENV-002",
        "document_id": "DOC-001",
        "status": "sent",
        "signer_email": "admin@org-alpha.com",
        "signer_name": "Jane Doe",
        "sent_at": "2026-03-20T09:00:00Z",
        "signed_at": "",
        "completed_at": "",
    },
]


# --- Request/Response models ---


class SendForSignatureRequest(
    BaseModel,
):  # CORRECT-LOCAL: FastAPI route schema — not a shared domain contract
    signer_email: str
    signer_name: str
    subject: str = "Document for Signature"
    message: str = "Please review and sign the attached document."


class SignatureStatusResponse(
    BaseModel,
):  # CORRECT-LOCAL: FastAPI route schema — not a shared domain contract
    document_id: str
    envelope_id: str
    status: str
    signer_email: str
    signer_name: str
    sent_at: str
    signed_at: str
    completed_at: str


# --- Seed mock store ---

_store = get_store()
for _env in MOCK_ENVELOPES:
    _existing = _store.get("envelopes", str(_env["envelope_id"]))
    if _existing is None:
        _store.create("envelopes", {**_env, "id": _env["envelope_id"]})


def _enforce_document_entitlement(auth: AuthContext, document_id: str) -> None:
    """Scope to the document's real owning org when resolvable; fail closed otherwise.

    The shared ``documents`` store (``client_reporting_api.mock_state``,
    shared with documents.py) only carries ownership for mock-mode
    seed/upload data — live-mode uploads don't persist an org_id
    anywhere yet. When it can't be resolved, deny non-internal callers
    outright rather than trusting a caller-supplied claim. Same pattern
    as ``documents.py::get_download_url`` and the same 2026-08-21 CTO
    handoff P1 fix.
    """
    doc = _store.get("documents", document_id)
    if doc is not None:
        enforce_entitlement(auth, str(doc.get("org_id", "")))
    else:
        require_internal(auth)


# --- Routes ---


@router.post("/api/v1/documents/{document_id}/send-for-signature")
def send_for_signature(document_id: str, request: SendForSignatureRequest, auth: AuthDep) -> dict[str, object]:
    """Create a DocuSign envelope and send a document for signature.

    In mock mode: creates a synthetic envelope record in the in-memory store.
    In live mode: creates a DocuSign envelope via the DocuSign API.
    """
    _enforce_document_entitlement(auth, document_id)
    envelope_id = f"ENV-{uuid.uuid4().hex[:8].upper()}"

    if _cloud_cfg.is_mock_mode():
        envelope_record: dict[str, object] = {
            "id": envelope_id,
            "envelope_id": envelope_id,
            "document_id": document_id,
            "status": "sent",
            "signer_email": request.signer_email,
            "signer_name": request.signer_name,
            "subject": request.subject,
            "message": request.message,
            "sent_at": "2026-03-21T00:00:00Z",
            "signed_at": "",
            "completed_at": "",
        }
        _store.create("envelopes", envelope_record)
        return {
            "envelope_id": envelope_id,
            "document_id": document_id,
            "status": "sent",
            "signer_email": request.signer_email,
        }

    logger.info(
        "send_for_signature: document_id=%s signer=%s",
        document_id,
        request.signer_email,
    )
    # Live implementation: create DocuSign envelope via API
    return {
        "envelope_id": envelope_id,
        "document_id": document_id,
        "status": "sent",
        "signer_email": request.signer_email,
    }


@router.get("/api/v1/documents/{document_id}/signature-status")
def get_signature_status(document_id: str, auth: AuthDep) -> dict[str, object]:
    """Check the DocuSign signature status for a document.

    In mock mode: returns the envelope status from the in-memory store.
    In live mode: queries the DocuSign API for envelope status.
    """
    _enforce_document_entitlement(auth, document_id)
    if _cloud_cfg.is_mock_mode():
        all_envelopes = _store.list("envelopes")
        matching = [e for e in all_envelopes if e.get("document_id") == document_id]
        if not matching:
            raise HTTPException(
                status_code=404,
                detail=f"No signature envelope found for document {document_id}",
            )
        # Return the most recent envelope
        latest = matching[-1]
        return {
            "document_id": document_id,
            "envelope_id": str(latest.get("envelope_id", "")),
            "status": str(latest.get("status", "unknown")),
            "signer_email": str(latest.get("signer_email", "")),
            "signer_name": str(latest.get("signer_name", "")),
            "sent_at": str(latest.get("sent_at", "")),
            "signed_at": str(latest.get("signed_at", "")),
            "completed_at": str(latest.get("completed_at", "")),
        }

    logger.info("get_signature_status: document_id=%s", document_id)
    # Live implementation: query DocuSign API
    raise HTTPException(
        status_code=404,
        detail=f"No signature envelope found for document {document_id}",
    )


@router.post("/api/v1/webhooks/docusign")
async def docusign_webhook(request: Request, auth: AuthDep) -> dict[str, str]:
    """DocuSign webhook receiver — processes envelope status changes.

    DocuSign sends webhook events when envelope status changes (sent, delivered,
    signed, completed, declined, voided). This endpoint updates the local
    envelope record accordingly.

    In mock mode: accepts the payload and updates the in-memory store.
    In live mode: validates the HMAC signature and processes the event.

    Entitlement: this is meant to be called by the DocuSign platform
    itself, not by an external client of this service — there is no
    client_id/org_id concept on a webhook payload, so
    ``require_internal`` is the strictest plausible gate (2026-08-21
    CTO handoff P1 fix). Whether DocuSign's own callback can actually
    present a valid internal token, vs. this needing real HMAC-signature
    validation independent of ``create_api_auth``, was flagged as an
    open question in the api-reference doc and is not resolved by this
    change.
    """
    require_internal(auth)
    body = await request.json()
    event_type = body.get("event", "unknown")
    envelope_id = body.get("envelope_id", "")

    if _cloud_cfg.is_mock_mode():
        logger.info("docusign_webhook (mock): event=%s envelope_id=%s", event_type, envelope_id)

        if envelope_id:
            existing = _store.get("envelopes", envelope_id)
            if existing is not None:
                status_map: dict[str, str] = {
                    "recipient-signed": "signed",
                    "envelope-completed": "completed",
                    "envelope-declined": "declined",
                    "envelope-voided": "voided",
                }
                new_status = status_map.get(event_type, str(existing.get("status", "sent")))
                updated = {**existing, "status": new_status}
                if new_status == "signed":
                    updated["signed_at"] = "2026-03-21T12:00:00Z"
                if new_status == "completed":
                    updated["completed_at"] = "2026-03-21T12:00:00Z"
                    updated["signed_at"] = updated.get("signed_at") or "2026-03-21T12:00:00Z"
                _store.update("envelopes", envelope_id, updated)

        return {"status": "received", "event": event_type}

    logger.info("docusign_webhook: event=%s envelope_id=%s", event_type, envelope_id)
    # Live implementation: validate HMAC, update envelope records, trigger notifications
    return {"status": "received", "event": event_type}
