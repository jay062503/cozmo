# tests/test_voice_parser.py
# Unit tests for voice command parsing.
# Mirror of VoiceCommandParserTests.swift.
# Run with: pytest tests/

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from backend.services.voice_parser import parse


# ── Nameplate ────────────────────────────────────────────────────────────────

def test_capture_nameplate_exact():
    assert parse("capture nameplate").kind == "nameplate"

def test_capture_nameplate_bare():
    assert parse("nameplate").kind == "nameplate"

def test_capture_nameplate_mixed_case():
    assert parse("Capture Nameplate").kind == "nameplate"

def test_capture_nameplate_with_filler():
    assert parse("um capture nameplate").kind == "nameplate"


# ── Damage ───────────────────────────────────────────────────────────────────

def test_capture_damage_full():
    cmd = parse("capture damage kitchen north wall")
    assert cmd.kind    == "damage"
    assert "kitchen"    in cmd.room
    assert "north wall" in cmd.surface

def test_capture_damage_bathroom_ceiling():
    cmd = parse("capture damage bathroom ceiling")
    assert cmd.kind   == "damage"
    assert "bathroom"  in cmd.room
    assert "ceiling"   in cmd.surface

def test_capture_damage_no_room():
    cmd = parse("damage floor")
    assert cmd.kind == "damage"

def test_capture_damage_basement():
    cmd = parse("capture damage basement south wall")
    assert "basement"   in cmd.room
    assert "south wall" in cmd.surface


# ── Note ─────────────────────────────────────────────────────────────────────

def test_note_with_colon():
    cmd = parse("note: subfloor soft by the cabinet")
    assert cmd.kind == "note"
    assert "subfloor" in cmd.text.lower()

def test_note_without_colon():
    cmd = parse("note subfloor feels soft near the sink")
    assert cmd.kind == "note"
    assert "subfloor" in cmd.text.lower()

def test_note_preserves_original_case():
    cmd = parse("note: Check the GFCI outlet")
    assert "GFCI" in cmd.text

def test_note_urgent():
    cmd = parse("note: urgent — sump pump running continuously")
    assert cmd.kind == "note"


# ── Unknown ──────────────────────────────────────────────────────────────────

def test_unknown_random_speech():
    assert parse("hello is this working").kind == "unknown"

def test_unknown_empty():
    assert parse("").kind == "unknown"
