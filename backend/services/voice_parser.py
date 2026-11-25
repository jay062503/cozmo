# services/voice_parser.py
# Parses raw speech transcript into one of three structured commands.
# Identical logic to VoiceCommandParser.swift.
#
# Commands:
#   "capture nameplate"
#   "capture damage [room] [surface]"
#   "note: [free text]"
#
# The fourth command for the live session ("capture moisture reading [value] [location]")
# is wired as a commented extension point at the bottom.

import re
from dataclasses import dataclass
from typing import Optional, Tuple

FILLER = re.compile(r'\b(um|uh|er|like)\b', re.IGNORECASE)

KNOWN_ROOMS = [
    "kitchen", "bathroom", "bedroom", "living room", "basement",
    "garage", "hallway", "laundry", "attic", "crawl space",
]
KNOWN_SURFACES = [
    "north wall", "south wall", "east wall", "west wall",
    "ceiling", "floor", "subfloor", "cabinet", "door", "window",
]


@dataclass
class ParsedCommand:
    kind: str           # "nameplate" | "damage" | "note" | "unknown"
    room: Optional[str] = None
    surface: Optional[str] = None
    text: Optional[str] = None      # for notes
    raw: str = ""


def parse(transcript: str) -> ParsedCommand:
    clean = FILLER.sub("", transcript).strip().lower()
    raw   = transcript.strip()

    # ── capture nameplate ────────────────────────────────────────────────────
    if "capture nameplate" in clean or clean.startswith("nameplate"):
        return ParsedCommand(kind="nameplate", raw=raw)

    # ── capture damage [room] [surface] ──────────────────────────────────────
    if "damage" in clean or "capture damage" in clean:
        room, surface = _extract_room_surface(clean)
        return ParsedCommand(kind="damage", room=room, surface=surface, raw=raw)

    # ── note: [text] ──────────────────────────────────────────────────────────
    note_match = re.match(r'^note\s*:?\s*(.+)', raw, re.IGNORECASE)
    if note_match:
        return ParsedCommand(kind="note", text=note_match.group(1).strip(), raw=raw)

    # ── Extension point: capture moisture reading [value] [location] ─────────
    # Uncomment for live-session task:
    # moisture_match = re.match(r'capture\s+moisture\s+reading\s+([\d.]+%?)\s+(.+)', clean)
    # if moisture_match:
    #     return ParsedCommand(kind="moisture",
    #                          text=moisture_match.group(1),
    #                          room=moisture_match.group(2).strip(),
    #                          raw=raw)

    return ParsedCommand(kind="unknown", raw=raw)


def _extract_room_surface(text: str) -> Tuple[str, str]:
    room    = next((r for r in KNOWN_ROOMS    if r in text), "unknown room")
    surface = next((s for s in KNOWN_SURFACES if s in text), "unknown surface")
    return room, surface
