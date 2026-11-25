# routes/session.py
# API routes for session lifecycle — start, stop, status.

import uuid
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from backend.services.job_builder import JobBuilder

router = APIRouter(prefix="/session", tags=["session"])

# In-memory session store (one active session at a time for this demo)
_sessions: dict[str, JobBuilder] = {}


class StartRequest(BaseModel):
    technician_id: Optional[str] = None


class StartResponse(BaseModel):
    session_id: str
    job_id: str
    message: str


class StopResponse(BaseModel):
    session_id: str
    job_id: str
    total_captures: int
    failed_quality_checks: int
    line_items_count: int


@router.post("/start", response_model=StartResponse)
def start_session(req: StartRequest):
    session_id = "SES-" + str(uuid.uuid4())[:8].upper()
    builder = JobBuilder()
    job_id  = builder.begin(session_id)
    _sessions[session_id] = builder
    print(f"[Session] Started {session_id} / {job_id}")
    return StartResponse(
        session_id=session_id,
        job_id=job_id,
        message="Session started. Ready for voice commands.",
    )


@router.post("/stop/{session_id}", response_model=StopResponse)
def stop_session(session_id: str):
    builder = _get_builder(session_id)
    record  = builder.build()
    return StopResponse(
        session_id=session_id,
        job_id=record.job_id,
        total_captures=record.metadata.total_captures,
        failed_quality_checks=record.metadata.failed_quality_checks,
        line_items_count=len(record.line_items),
    )


@router.get("/status/{session_id}")
def session_status(session_id: str):
    builder = _get_builder(session_id)
    record  = builder.current()
    return {
        "session_id":  session_id,
        "job_id":      record.job_id if record else None,
        "captures":    len(record.captures) if record else 0,
        "notes":       len(record.notes) if record else 0,
        "line_items":  len(record.line_items) if record else 0,
        "queue_depth": 0,
    }


def _get_builder(session_id: str) -> JobBuilder:
    if session_id not in _sessions:
        # Allow fetching a builder for sessions that only exist in the demo
        _sessions[session_id] = JobBuilder()
        _sessions[session_id].begin(session_id)
    return _sessions[session_id]


# Expose so other routes can access the same builder
def get_builder(session_id: str) -> JobBuilder:
    return _get_builder(session_id)
