# routes/job_record.py
# Returns the current or completed job record for a session.

from fastapi import APIRouter, HTTPException
from backend.routes.session import get_builder

router = APIRouter(prefix="/job", tags=["job"])


@router.get("/{session_id}")
def get_job_record(session_id: str):
    builder = get_builder(session_id)
    record  = builder.current()
    if not record:
        raise HTTPException(status_code=404, detail="No active job for this session")
    return record.model_dump(mode="json")


@router.get("/{session_id}/line_items")
def get_line_items(session_id: str):
    builder = get_builder(session_id)
    record  = builder.current()
    if not record:
        return []
    return [li.model_dump() for li in record.line_items]
