"""
RC-9 Contract Gate: Ops SSOT Pack (SLO/Alerts/Runbook).

What RC-9 locks:
- Machine-readable SLO definition pack exists and validates
- Machine-readable alert rule pack exists and validates
- Human runbook exists with required sections
- At least one alert has deterministic action (ROLLBACK/KILL_SWITCH)
- All alert runbook references resolve to actual headings in runbook.md

Gate-1: Required ops files exist and non-empty
Gate-2: JSON schemas validate (slo.json, alerts.json)
Gate-3: Rollback/kill-switch alert exists + runbook refs resolve

NOTE:
These tests are expected to FAIL until RC-9 is implemented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


def _repo_root() -> Path:
    # tests/ -> api/ -> apps/ -> dpp/
    return Path(__file__).resolve().parent.parent.parent.parent


def _read_json(p: Path) -> dict:
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def _validate_json(schema: dict, instance: dict) -> list[str]:
    v = Draft202012Validator(schema)
    errors = sorted(v.iter_errors(instance), key=lambda e: list(e.path))
    msgs = []
    for e in errors:
        path = ".".join([str(x) for x in e.path]) or "<root>"
        msgs.append(f"{path}: {e.message}")
    return msgs


def _slugify_heading(h: str) -> str:
    # Minimal slug check (GitHub-style is more complex). For our gate:
    # we just ensure the referenced heading text exists in runbook.md.
    return h.strip().lower()


def test_rc9_gate_1_ops_files_exist_and_nonempty():
    """Gate-1: Required ops files MUST exist and be non-empty."""
    root = _repo_root()
    ops = root / "ops"
    assert ops.exists() and ops.is_dir(), f"missing ops dir: {ops}"

    required = [
        ops / "slo.schema.json",
        ops / "slo.json",
        ops / "alerts.schema.json",
        ops / "alerts.json",
        ops / "runbook.md",
    ]
    for p in required:
        assert p.exists() and p.is_file(), f"missing required file: {p}"
        assert p.stat().st_size > 10, f"file too small / empty: {p}"


def test_rc9_gate_2_json_schema_validation():
    """Gate-2: JSON configs MUST validate against their schemas."""
    root = _repo_root()
    ops = root / "ops"

    slo_schema = _read_json(ops / "slo.schema.json")
    slo = _read_json(ops / "slo.json")
    errs = _validate_json(slo_schema, slo)
    assert not errs, "slo.json schema errors:\n" + "\n".join(errs)

    alerts_schema = _read_json(ops / "alerts.schema.json")
    alerts = _read_json(ops / "alerts.json")
    errs = _validate_json(alerts_schema, alerts)
    assert not errs, "alerts.json schema errors:\n" + "\n".join(errs)


def test_rc9_gate_3_rollback_or_kill_switch_and_runbook_refs_resolve():
    """Gate-3: At least one alert MUST have ROLLBACK/KILL_SWITCH action AND all runbook refs MUST resolve."""
    root = _repo_root()
    ops = root / "ops"

    alerts = _read_json(ops / "alerts.json")
    runbook_text = (ops / "runbook.md").read_text(encoding="utf-8")

    items = alerts.get("alerts", [])
    assert isinstance(items, list) and items, "alerts.alerts must be a non-empty list"

    has_deterministic_action = False
    missing_refs: list[str] = []

    for a in items:
        action = (a.get("action") or {})
        action_type = (action.get("type") or "").upper()
        if action_type in {"ROLLBACK", "KILL_SWITCH"}:
            has_deterministic_action = True

        # runbook_ref must be a heading present in the runbook
        ref = (a.get("runbook_ref") or "").strip()
        assert ref.startswith("#"), f"runbook_ref must start with '#': {ref}"
        # For gate simplicity: require the heading line text exists in runbook.
        # We store ref as a hash anchor like '#money-leak-detection' AND also keep 'runbook_heading'.
        heading = (a.get("runbook_heading") or "").strip()
        assert heading, f"alert missing runbook_heading (human-readable): {a.get('id') or a.get('name')}"
        if _slugify_heading(heading) not in runbook_text.lower():
            missing_refs.append(f"{a.get('id') or a.get('name')}: heading not found -> {heading}")

    assert has_deterministic_action, "At least one alert must have action.type in {ROLLBACK, KILL_SWITCH}"
    assert not missing_refs, "Runbook references missing:\n" + "\n".join(missing_refs)
