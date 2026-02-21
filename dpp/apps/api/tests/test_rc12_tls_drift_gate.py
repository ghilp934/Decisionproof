"""RC-12 Contract Gate: TLS / DB URL Drift Guard.

Prevents 'sslmode=disable' (a CI/local-only setting) from leaking into
staging/prod deployment assets (k8s manifests, Terraform, ops scripts).

Test matrix:
  T1 – repo_scan_has_no_sslmode_disable_in_scope
       Runs the drift gate against the actual repository tree.
       PASS criteria: exit 0, report.ok=True, scanned_files > 0.

  T2 – fixture_with_disable_fails
       Creates a synthetic staging manifest containing 'sslmode=disable'
       in a temp directory and runs the gate against it.
       PASS criteria: exit 2, report.ok=False, exactly 1 hit with
       excerpt == 'sslmode=disable'.
"""

import json
import subprocess
import sys
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

# parents[0] = dpp/apps/api/tests/
# parents[1] = dpp/apps/api/
# parents[2] = dpp/apps/
# parents[3] = dpp/   ← DPP_ROOT (k8s/, infra/, ops/ live here)
DPP_ROOT = Path(__file__).resolve().parents[3]
DRIFT_GATE = DPP_ROOT / "tools" / "security" / "tls_drift_gate.py"

# Evidence path (written during local RC run and CI)
_EVIDENCE_OUT = DPP_ROOT / "evidence" / "security" / "tls_drift_gate_report.json"


# ── Helper ────────────────────────────────────────────────────────────────────

def _run_gate(root: Path, out_path: Path) -> tuple[int, dict]:
    """Invoke tls_drift_gate CLI and return (exit_code, report_dict)."""
    cmd = [
        sys.executable,
        str(DRIFT_GATE),
        "--root", str(root),
        "--out", str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    report: dict = {}
    if out_path.exists():
        try:
            report = json.loads(out_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return result.returncode, report


def _save_evidence(report: dict) -> None:
    """Persist the scan report to the standard evidence path (best-effort)."""
    try:
        _EVIDENCE_OUT.parent.mkdir(parents=True, exist_ok=True)
        _EVIDENCE_OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    except OSError:
        pass  # Evidence write failure must not block the test result


# ── Test class ────────────────────────────────────────────────────────────────

class TestRC12TLSDriftGate:
    """RC-12: Enforce sslmode=disable absence in staging/prod deployment assets."""

    # ── T1 ────────────────────────────────────────────────────────────────────

    def test_t1_repo_scan_has_no_sslmode_disable_in_scope(self, tmp_path) -> None:
        """T1: Drift gate runs on actual repo tree and finds zero hits.

        Scans k8s/, infra/, ops/scripts/ with their locked include/exclude
        globs (defined in tls_drift_gate.py).  Asserts:
          - exit code == 0
          - report["ok"] is True
          - report["scanned_files"] > 0  (confirms globs matched real files)
        """
        out = tmp_path / "report_t1.json"
        code, report = _run_gate(DPP_ROOT, out)

        # Persist evidence copy (non-fatal if fails)
        _save_evidence(report)

        assert code == 0, (
            f"RC-12 FAIL: sslmode=disable found in staging/prod deployment asset!\n"
            f"Hits: {report.get('hits', [])}\n"
            "Action: remove sslmode=disable from the affected file, or move the\n"
            "setting into a CI-only workflow (.github/workflows/**) which is\n"
            "explicitly excluded from this scan."
        )
        assert report.get("ok") is True, (
            "report.ok must be True when exit code == 0"
        )
        assert report.get("scanned_files", 0) > 0, (
            "scanned_files must be > 0 — include globs did not match any files.\n"
            "Check that k8s/, infra/, ops/scripts/ exist under DPP_ROOT."
        )

    # ── T2 ────────────────────────────────────────────────────────────────────

    def test_t2_fixture_with_disable_fails(self, tmp_path) -> None:
        """T2: Synthetic staging manifest with sslmode=disable is detected.

        Creates a temp directory with a fake k8s/overlays/staging/api-deployment.yaml
        that contains the banned string. Asserts:
          - exit code == 2
          - report["ok"] is False
          - exactly 1 hit
          - hit["excerpt"] == "sslmode=disable"  (constant, no credentials)
          - hit["path"] references the fixture file
        """
        # Build fake staging manifest — use <PLACEHOLDER> instead of real credentials
        fake_manifest = (
            tmp_path / "k8s" / "overlays" / "staging" / "api-deployment.yaml"
        )
        fake_manifest.parent.mkdir(parents=True, exist_ok=True)
        fake_manifest.write_text(
            "env:\n"
            "  - name: DATABASE_URL\n"
            "    value: postgresql://postgres:<PLACEHOLDER>@localhost:5432/dpp?sslmode=disable\n",
            encoding="utf-8",
        )

        out = tmp_path / "report_t2.json"
        code, report = _run_gate(tmp_path, out)

        assert code == 2, (
            f"Expected exit code 2 (drift detected), got {code}.\n"
            "tls_drift_gate did not flag sslmode=disable in the fixture file.\n"
            "Check that k8s/**/*.yaml is still in DEFAULT_INCLUDE_GLOBS."
        )
        assert report.get("ok") is False, (
            "report.ok must be False when hits exist"
        )

        hits = report.get("hits", [])
        assert len(hits) == 1, (
            f"Expected exactly 1 hit, got {len(hits)}: {hits}"
        )

        hit = hits[0]
        assert hit["excerpt"] == "sslmode=disable", (
            f"Excerpt must be the constant 'sslmode=disable', got: {hit['excerpt']!r}\n"
            "Full-line excerpts (which may contain DB credentials) are forbidden."
        )
        assert "api-deployment.yaml" in hit["path"], (
            f"Hit path must reference the fixture file, got: {hit['path']!r}"
        )
