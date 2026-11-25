# services/job_builder.py
# Accumulates captures and notes during a session, generates line items,
# and assembles the final JobRecord.  Mirrors JobRecordBuilder.swift.

from __future__ import annotations
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from backend.models.job_record import (
    JobRecord, CaptureEvent, StructuredNote, LineItem,
    JobMetadata, ConnectivityEvent, ExtractionResult,
    QualityCheckResult, NoteExtraction, ConfidenceField,
)
from backend.models.line_item_catalog import matches_for


class JobBuilder:
    def __init__(self):
        self._record: Optional[JobRecord] = None

    # ── Session lifecycle ────────────────────────────────────────────────────

    def begin(self, session_id: str) -> str:
        job_id = "JOB-" + str(uuid.uuid4())[:8].upper()
        self._record = JobRecord(
            job_id=job_id,
            session_id=session_id,
            started_at=datetime.utcnow(),
            status="in_progress",
        )
        self._record.job_id = job_id
        return job_id

    @property
    def job_id(self) -> str:
        return self._record.job_id if self._record else ""

    # ── Add capture ──────────────────────────────────────────────────────────

    def add_capture(
        self,
        capture_type: str,
        frame_id: str,
        image_path: Optional[str],
        quality: QualityCheckResult,
        extraction: ExtractionResult,
    ) -> CaptureEvent:
        event = CaptureEvent(
            id=frame_id,
            capture_type=capture_type,
            image_local_path=image_path,
            quality_check=quality,
            extraction=extraction,
            upload_status="queued",
        )
        self._record.captures.append(event)
        self._regenerate_line_items()
        return event

    # ── Add note ─────────────────────────────────────────────────────────────

    def add_note(self, raw_text: str, extraction: NoteExtraction) -> StructuredNote:
        note = StructuredNote(
            raw_voice_text=raw_text,
            extraction=extraction,
        )
        self._record.notes.append(note)
        self._regenerate_line_items()
        return note

    # ── Connectivity ─────────────────────────────────────────────────────────

    def record_connectivity(self, event_type: str, queued: int):
        self._record.metadata.connectivity_events.append(
            ConnectivityEvent(event_type=event_type, queued_items_at_event=queued)
        )

    # ── Build final record ───────────────────────────────────────────────────

    def build(self, device_mode: str = "android_browser") -> JobRecord:
        r = self._record
        r.completed_at = datetime.utcnow()
        r.status = "pending_upload"
        r.metadata = JobMetadata(
            app_version="1.0.0",
            sdk_version="DAT-1.0-Web-Sim",
            device_mode=device_mode,
            connectivity_events=r.metadata.connectivity_events,
            total_captures=len(r.captures),
            failed_quality_checks=sum(1 for c in r.captures if not c.quality_check.passed),
            retake_requests=sum(1 for c in r.captures if not c.quality_check.passed),
        )
        return r

    def current(self) -> Optional[JobRecord]:
        return self._record

    # ── Line item generation ─────────────────────────────────────────────────

    def _regenerate_line_items(self):
        seen: Dict[str, LineItem] = {}

        for cap in self._record.captures:
            text  = self._describe(cap.extraction)
            conf  = self._confidence(cap.extraction)
            size  = self._size(cap.extraction)

            for entry in matches_for(text):
                if entry.code in seen:
                    existing = seen[entry.code]
                    existing.source_captures.append(cap.id)
                    if size and entry.unit in ("sq_ft", "sq_m"):
                        qty = float(size) * 10.764   # m² → sq_ft
                        existing.estimated_quantity = (existing.estimated_quantity or 0) + qty
                else:
                    qty = float(size) * 10.764 if size else None
                    seen[entry.code] = LineItem(
                        code=entry.code,
                        description=entry.description,
                        unit=entry.unit,
                        estimated_quantity=round(qty, 1) if qty else None,
                        confidence=round(conf, 2),
                        source_captures=[cap.id],
                    )

        for note in self._record.notes:
            for entry in matches_for(note.extraction.raw_text):
                if entry.code not in seen:
                    seen[entry.code] = LineItem(
                        code=entry.code,
                        description=entry.description,
                        unit=entry.unit,
                        confidence=0.60,
                        source_captures=[],
                    )

        self._record.line_items = sorted(seen.values(), key=lambda x: x.code)

    def _describe(self, result: ExtractionResult) -> str:
        p = result.payload
        if result.type == "nameplate":
            return " ".join(filter(None, [p.make.value, p.model.value, "water heater appliance"]))
        if result.type == "damage":
            return " ".join(filter(None, [p.room.value, p.surface.value, p.damage_type.value, p.severity_level.value]))
        return getattr(p, "raw_text", "")

    def _confidence(self, result: ExtractionResult) -> float:
        return getattr(result.payload, "overall_confidence", 0.5)

    def _size(self, result: ExtractionResult) -> Optional[str]:
        if result.type == "damage":
            return result.payload.rough_size_m2.value
        return None
