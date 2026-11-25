# models/line_item_catalog.py
# 15 estimate line-item codes — identical to LineItemCatalog.swift.

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class CatalogEntry:
    code: str
    description: str
    unit: str
    trigger_keywords: List[str]


CATALOG: List[CatalogEntry] = [
    CatalogEntry("DRY-RR-SF",      "Remove and replace drywall",                     "sq_ft",     ["drywall", "wall", "ceiling", "plaster", "sheetrock"]),
    CatalogEntry("INS-RR-SF",      "Remove and replace insulation (batt)",            "sq_ft",     ["insulation", "batt", "fiberglass"]),
    CatalogEntry("FLR-SUB-SF",     "Remove and replace subfloor (3/4\" OSB)",         "sq_ft",     ["subfloor", "sub-floor", "floor sheathing", "soft floor"]),
    CatalogEntry("FLR-HAR-SF",     "Remove and replace hardwood flooring",            "sq_ft",     ["hardwood", "wood floor", "engineered wood"]),
    CatalogEntry("FLR-CAR-SF",     "Remove and replace carpet and pad",               "sq_ft",     ["carpet", "carpeting", "rug", "pad"]),
    CatalogEntry("FLR-TIL-SF",     "Remove and replace ceramic/porcelain tile",       "sq_ft",     ["tile", "ceramic", "porcelain", "grout"]),
    CatalogEntry("MOLD-TREAT-SF",  "Mold remediation treatment",                      "sq_ft",     ["mold", "mildew", "black spot", "fungus", "microbial"]),
    CatalogEntry("PAINT-WALL-SF",  "Paint walls (2 coats)",                           "sq_ft",     ["paint", "stain", "discoloration", "water stain"]),
    CatalogEntry("EQUIP-WH-EA",    "Water heater replacement (40-gal electric)",      "each",      ["water heater", "hot water heater", "water tank"]),
    CatalogEntry("EQUIP-DH-EA",    "Dehumidifier placement (commercial)",             "each",      ["humid", "moisture", "wet", "water damage", "flood"]),
    CatalogEntry("EQUIP-FAN-EA",   "Air mover / drying fan placement",                "each",      ["dry out", "air mover", "fan", "drying"]),
    CatalogEntry("DEMO-HAZ-HR",    "Hazardous material handling (asbestos/lead)",     "hours",     ["asbestos", "lead", "popcorn ceiling", "textured ceiling", "pre-1980"]),
    CatalogEntry("TRIM-BASE-LF",   "Remove and replace baseboard trim",               "linear_ft", ["baseboard", "trim", "molding"]),
    CatalogEntry("STRUC-BEAM-LF",  "Structural beam assessment and repair",           "linear_ft", ["beam", "joist", "structural", "framing", "rot"]),
    CatalogEntry("CONTENT-PACK-HR","Contents pack-out and inventory",                 "hours",     ["contents", "furniture", "belongings", "pack out", "move out"]),
]


def matches_for(text: str) -> List[CatalogEntry]:
    lower = text.lower()
    return [e for e in CATALOG if any(kw in lower for kw in e.trigger_keywords)]


def find_by_code(code: str) -> Optional[CatalogEntry]:
    return next((e for e in CATALOG if e.code == code), None)
