# services/upload_queue.py
# Offline-first upload queue — mirrors UploadQueueService.swift.
# Captures are written to disk immediately; this service drains them
# when connectivity is available (simulated here as always-available on LAN).
#
# In the web version the queue lives server-side in a JSON file,
# because the browser already persisted the image on the server via /capture.

from __future__ import annotations
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import List
from dataclasses import dataclass, asdict

QUEUE_FILE = Path(__file__).parent.parent.parent / "uploads" / "queue.json"


@dataclass
class QueuedItem:
    id: str
    capture_id: str
    job_id: str
    image_path: str
    capture_type: str
    enqueued_at: str
    attempts: int
    status: str   # "pending" | "uploaded" | "failed"


class UploadQueue:
    def __init__(self):
        self._items: List[QueuedItem] = []
        self._load()

    def enqueue(self, capture_id: str, job_id: str, image_path: str, capture_type: str):
        item = QueuedItem(
            id=str(uuid.uuid4())[:8],
            capture_id=capture_id,
            job_id=job_id,
            image_path=image_path,
            capture_type=capture_type,
            enqueued_at=datetime.utcnow().isoformat(),
            attempts=0,
            status="pending",
        )
        self._items.append(item)
        self._save()
        print(f"[UploadQueue] Enqueued {capture_id}. Queue depth: {self.pending_count}")

    def mark_uploaded(self, capture_id: str):
        for item in self._items:
            if item.capture_id == capture_id:
                item.status = "uploaded"
        self._save()

    def mark_failed(self, capture_id: str):
        for item in self._items:
            if item.capture_id == capture_id:
                item.attempts += 1
                if item.attempts >= 3:
                    item.status = "failed"
        self._save()

    @property
    def pending_count(self) -> int:
        return sum(1 for i in self._items if i.status == "pending")

    def all_items(self) -> List[dict]:
        return [asdict(i) for i in self._items]

    def _save(self):
        QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(QUEUE_FILE, "w") as f:
            json.dump([asdict(i) for i in self._items], f, indent=2)

    def _load(self):
        if QUEUE_FILE.exists():
            with open(QUEUE_FILE) as f:
                raw = json.load(f)
            self._items = [QueuedItem(**r) for r in raw]
            # Reset in-flight items on restart
            for item in self._items:
                if item.status == "pending":
                    item.attempts = 0
            print(f"[UploadQueue] Loaded {len(self._items)} items, {self.pending_count} pending")


# Module-level singleton
queue = UploadQueue()
