# models/job_record.py
# Data models for FieldCapture — mirrors the iOS JobRecord.swift schema exactly.
# Using Python dataclasses + Pydantic so the JSON output is identical.

from __future__ import annotations
from typing import Optional, List, Literal
from pydantic import BaseModel, Field
from datetime import datetime
import uuid


# ── Shared ──────────────────────────────────────────────────────────────────

class ConfidenceField(BaseModel):
    value: Optional[str] = None
    confidence: float = 0.0          # 0.0 – 1.0
    source: str = "vision_api"       # "vision_api" | "regex" | "voice_command" | "inferred"


class QualityThresholds(BaseModel):
    min_blur_score: float = 80.0     # Laplacian variance proxy
    min_brightness: float = 40.0     # mean pixel intensity 0–255
    min_framing: float = 0.15        # subject coverage 0–1


class QualityCheckResult(BaseModel):
    passed: bool
    blur_score: float
    brightness_score: float
    framing_score: float
    failure_reasons: List[str] = []
    thresholds_used: QualityThresholds = QualityThresholds()


# ── Extraction results ───────────────────────────────────────────────────────

class NameplateExtraction(BaseModel):
    make: ConfidenceField
    model: ConfidenceField
    serial_number: ConfidenceField
    manufacture_date: ConfidenceField
    raw_text: str = ""
    overall_confidence: float = 0.0


class DamageExtraction(BaseModel):
    room: ConfidenceField
    surface: ConfidenceField
    damage_type: ConfidenceField
    rough_size_m2: ConfidenceField
    severity_level: ConfidenceField
    overall_confidence: float = 0.0


class NoteExtraction(BaseModel):
    raw_text: str
    location: Optional[ConfidenceField] = None
    subject: Optional[ConfidenceField] = None
    urgency: Optional[ConfidenceField] = None
    overall_confidence: float = 0.85


class ExtractionResult(BaseModel):
    type: Literal["nameplate", "damage", "note"]
    # Union stored as-is; we discriminate manually on `type` when needed.
    # Pydantic v2 validates whichever concrete class matches the incoming data.
    payload: NameplateExtraction | DamageExtraction | NoteExtraction

    model_config = {"arbitrary_types_allowed": True}


# ── Capture event ────────────────────────────────────────────────────────────

class CaptureEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    capture_type: Literal["nameplate", "damage", "note"]
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    image_local_path: Optional[str] = None
    image_remote_url: Optional[str] = None
    quality_check: QualityCheckResult
    extraction: ExtractionResult
    upload_status: Literal["uploaded", "queued", "failed"] = "queued"


# ── Note ─────────────────────────────────────────────────────────────────────

class StructuredNote(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    raw_voice_text: str
    extraction: NoteExtraction


# ── Line item ────────────────────────────────────────────────────────────────

class LineItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    code: str
    description: str
    unit: str
    estimated_quantity: Optional[float] = None
    confidence: float
    source_captures: List[str] = []


# ── Connectivity ─────────────────────────────────────────────────────────────

class ConnectivityEvent(BaseModel):
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    event_type: str    # "lost" | "restored"
    queued_items_at_event: int = 0


# ── Job metadata ─────────────────────────────────────────────────────────────

class JobMetadata(BaseModel):
    app_version: str = "1.0.0"
    sdk_version: str = "DAT-1.0-Web-Sim"
    device_mode: str = "android_browser"
    connectivity_events: List[ConnectivityEvent] = []
    total_captures: int = 0
    failed_quality_checks: int = 0
    retake_requests: int = 0


# ── Top-level job record ──────────────────────────────────────────────────────

class JobRecord(BaseModel):
    job_id: str = Field(default_factory=lambda: "JOB-" + str(uuid.uuid4())[:8].upper())
    session_id: str = ""
    technician_id: Optional[str] = None
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    status: Literal["in_progress", "completed", "uploaded", "pending_upload"] = "in_progress"
    captures: List[CaptureEvent] = []
    notes: List[StructuredNote] = []
    line_items: List[LineItem] = []
    metadata: JobMetadata = JobMetadata()
