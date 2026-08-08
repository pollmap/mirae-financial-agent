"""Loaders for the scope-neutral concept catalog and comparability matrix.

These CSVs under ``registry/semantic/`` are the single source of truth for
which physical metric each financial concept binds to per product scope, and
for how two scopes may be presented together. ``ABSENT`` is a first-class
binding value: it routes a scope to the alternatives path instead of either
refusing or inventing data.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

SEMANTIC_ROOT = Path(__file__).resolve().parents[2] / "registry" / "semantic"

ABSENT = "ABSENT"

# Weakest-wins ordering when a plan spans more than two scopes.
MODE_STRENGTH = {"UNIFIED_RANK": 3, "SPLIT_PRESENTATION": 2, "EXPLAIN_ONLY": 1}


@dataclass(frozen=True, slots=True)
class Concept:
    concept_id: str
    concept_kind: str
    label_ko: str
    direction: str
    bindings: dict[str, str]  # scope -> physical metric id | catalog field | ABSENT
    unit_concept: str
    notes: str


@dataclass(frozen=True, slots=True)
class Comparability:
    mode: str  # UNIFIED_RANK | SPLIT_PRESENTATION | EXPLAIN_ONLY | SCOPE_ABSENT
    warnings: tuple[str, ...]
    basis_note_ko: str


@dataclass(frozen=True, slots=True)
class ConceptCatalog:
    concepts: dict[str, Concept]
    physical_to_concept: dict[str, str]

    def resolve_metric_concept(self, metric_id: str) -> str | None:
        """Map a plan metric (physical, cross.* placeholder, or concept id)."""

        if metric_id in self.concepts:
            return metric_id
        if metric_id in self.physical_to_concept:
            return self.physical_to_concept[metric_id]
        if metric_id.startswith("cross."):
            suffix = metric_id.split(".", 1)[1]
            if suffix in self.concepts:
                return suffix
            # Placeholder ids minted by the deterministic planner use the
            # physical suffix (e.g. cross.aum_last), not the concept id.
            for concept_id, concept in self.concepts.items():
                for binding in concept.bindings.values():
                    if binding != ABSENT and binding.split(".", 1)[-1] == suffix:
                        return concept_id
        return None

    def binding(self, concept_id: str, scope: str) -> str | None:
        concept = self.concepts.get(concept_id)
        if concept is None:
            return None
        bound = concept.bindings.get(scope, ABSENT)
        return None if bound == ABSENT else bound


@lru_cache(maxsize=1)
def load_concept_catalog(path: Path | None = None) -> ConceptCatalog:
    source = path or SEMANTIC_ROOT / "concept_catalog_v1.csv"
    concepts: dict[str, Concept] = {}
    physical: dict[str, str] = {}
    with source.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            bindings: dict[str, str] = {}
            for pair in row["scope_bindings"].split("|"):
                scope, _, bound = pair.partition(":")
                bindings[scope.strip()] = bound.strip() or ABSENT
            concept = Concept(
                concept_id=row["concept_id"].strip(),
                concept_kind=row["concept_kind"].strip(),
                label_ko=row["label_ko"].strip(),
                direction=row["direction"].strip(),
                bindings=bindings,
                unit_concept=row["unit_concept"].strip(),
                notes=(row.get("notes") or "").strip(),
            )
            concepts[concept.concept_id] = concept
            for bound in bindings.values():
                if bound != ABSENT and "." in bound and not bound.startswith("product."):
                    physical[bound] = concept.concept_id
    return ConceptCatalog(concepts=concepts, physical_to_concept=physical)


@lru_cache(maxsize=1)
def load_comparability(path: Path | None = None) -> dict[tuple[str, frozenset[str]], Comparability]:
    source = path or SEMANTIC_ROOT / "comparability_matrix_v1.csv"
    matrix: dict[tuple[str, frozenset[str]], Comparability] = {}
    with source.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            key = (
                row["concept_id"].strip(),
                frozenset({row["scope_a"].strip(), row["scope_b"].strip()}),
            )
            matrix[key] = Comparability(
                mode=row["comparability"].strip(),
                warnings=tuple(
                    code.strip()
                    for code in row["warning_codes"].split("|")
                    if code.strip()
                ),
                basis_note_ko=row["basis_note_ko"].strip(),
            )
    return matrix
