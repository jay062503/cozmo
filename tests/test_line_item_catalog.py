# tests/test_line_item_catalog.py
# Mirror of LineItemCatalogTests.swift.

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from backend.models.line_item_catalog import matches_for, find_by_code, CATALOG


def test_drywall_matched():
    codes = [e.code for e in matches_for("water stain on drywall")]
    assert "DRY-RR-SF" in codes

def test_subfloor_matched():
    codes = [e.code for e in matches_for("subfloor soft by the cabinet")]
    assert "FLR-SUB-SF" in codes

def test_mold_matched():
    codes = [e.code for e in matches_for("black mold on ceiling")]
    assert "MOLD-TREAT-SF" in codes

def test_water_heater_matched():
    codes = [e.code for e in matches_for("water heater nameplate GE model")]
    assert "EQUIP-WH-EA" in codes

def test_carpet_matched():
    codes = [e.code for e in matches_for("carpet soaked water damage bedroom")]
    assert "FLR-CAR-SF" in codes

def test_no_match_unrelated():
    assert matches_for("sunny day outside the building") == []

def test_all_entries_have_codes():
    for entry in CATALOG:
        assert entry.code, f"Empty code for: {entry.description}"
        assert entry.trigger_keywords, f"No keywords for: {entry.code}"

def test_catalog_has_15_entries():
    assert len(CATALOG) >= 15

def test_find_by_code():
    entry = find_by_code("DRY-RR-SF")
    assert entry is not None
    assert entry.unit == "sq_ft"

def test_find_missing_code():
    assert find_by_code("NONEXISTENT") is None
