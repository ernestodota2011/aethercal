"""Tests for synthetic WhatsApp intent generation and benchmark dataset integrity."""

from __future__ import annotations

import json
from pathlib import Path

from simulation.scripts.generate_synthetic_intents import (
    MIN_THRESHOLDS,
    build_taxonomy,
    verify_dataset,
    write_benchmark_jsonl,
)


def test_build_taxonomy_meets_thresholds() -> None:
    """Verify that build_taxonomy yields sufficient samples for all target classes."""
    records = build_taxonomy()
    counts: dict[str, int] = {}
    for r in records:
        counts[r["expected_action"]] = counts.get(r["expected_action"], 0) + 1

    for action, min_req in MIN_THRESHOLDS.items():
        assert counts.get(action, 0) >= min_req, (
            f"Taxonomy contains only {counts.get(action, 0)} records for action '{action}', "
            f"expected >= {min_req}"
        )


def test_build_taxonomy_schema_and_types() -> None:
    """Validate that every record emitted by taxonomy follows exact TypedDict schema."""
    records = build_taxonomy()
    required_keys = {"text", "expected_action", "category", "dialect_or_case"}

    for idx, r in enumerate(records):
        assert set(r.keys()) == required_keys, f"Record {idx} has invalid keys: {r.keys()}"
        assert isinstance(r["text"], str)
        assert isinstance(r["expected_action"], str)
        assert isinstance(r["category"], str)
        assert isinstance(r["dialect_or_case"], str)


def test_generated_benchmark_file_on_disk_passes_verification() -> None:
    """Validate that the canonical benchmark JSONL file exists and satisfies verify_dataset."""
    repo_root = Path(__file__).resolve().parents[2]
    dataset_path = repo_root / "simulation" / "datasets" / "whatsapp_intents_benchmark.jsonl"

    assert dataset_path.is_file(), f"Expected dataset file at {dataset_path}"
    counts = verify_dataset(dataset_path)

    for action, min_req in MIN_THRESHOLDS.items():
        assert counts.get(action, 0) >= min_req


def test_write_and_verify_roundtrip(tmp_path: Path) -> None:
    """Test full generation, serialization to jsonl and verification in isolated path."""
    temp_jsonl = tmp_path / "test_intents.jsonl"
    records = build_taxonomy()
    written = write_benchmark_jsonl(records, temp_jsonl)
    assert written == len(records)

    counts = verify_dataset(temp_jsonl)
    assert sum(counts.values()) == len(records)

    # Check that each line is valid json
    with temp_jsonl.open("r", encoding="utf-8") as f:
        for line in f:
            parsed = json.loads(line)
            assert "text" in parsed
            assert "expected_action" in parsed
