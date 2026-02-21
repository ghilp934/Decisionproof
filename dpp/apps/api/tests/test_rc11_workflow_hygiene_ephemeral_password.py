"""RC-11 Contract Gate: Workflow Hygiene — Ephemeral Password Strategy.

Test matrix:
  A – Workflow file exists at expected path
  B – Banned literal "drillpw2026" is absent
  C – Ephemeral password strategy is present (job env + service env, both run-scoped)
  D – LOCAL_DB_URL uses the env var (no inline password)
  E – Ephemeral password is masked via ::add-mask::
"""

from pathlib import Path


# ── Helpers ──────────────────────────────────────────────────────────────────

def _workflow_path() -> Path:
    # Test file: dpp/apps/api/tests/test_rc11_...py
    # parents[4] → repo root (decisionwise_api_platform/)
    repo_root = Path(__file__).resolve().parents[4]
    return repo_root / ".github" / "workflows" / "phase45_db_rollback_drill.yml"


def _read_workflow() -> str:
    return _workflow_path().read_text(encoding="utf-8")


# ── Test class ────────────────────────────────────────────────────────────────

class TestRC11WorkflowHygieneEphemeralPassword:
    """RC-11: Prevent password-like literals in public workflows; enforce ephemeral per-run password."""

    # ── Test A ────────────────────────────────────────────────────────────────

    def test_a_workflow_file_exists(self) -> None:
        """A: Workflow file exists at the expected repo path."""
        wf_path = _workflow_path()
        assert wf_path.exists(), (
            f"Workflow file not found: {wf_path}\n"
            "Ensure phase45_db_rollback_drill.yml is present in .github/workflows/"
        )

    # ── Test B ────────────────────────────────────────────────────────────────

    def test_b_banned_literal_absent(self) -> None:
        """B: The banned literal 'drillpw2026' must not appear anywhere in the workflow."""
        text = _read_workflow()
        assert "drillpw2026" not in text, (
            "BANNED LITERAL FOUND: 'drillpw2026' still present in workflow.\n"
            "Replace with ephemeral per-run password (drill-${{ github.run_id }}-${{ github.run_attempt }})."
        )

    # ── Test C ────────────────────────────────────────────────────────────────

    def test_c_ephemeral_password_strategy_present(self) -> None:
        """C: Ephemeral password strategy is present in both job env and Postgres service env.

        Requires BOTH:
        - Job-level env: DRILL_PG_PASSWORD: drill-${{ github.run_id }}-${{ github.run_attempt }}
        - Service-level env: POSTGRES_PASSWORD: drill-${{ github.run_id }}-${{ github.run_attempt }}
        """
        text = _read_workflow()
        run_scoped_expr = "drill-${{ github.run_id }}-${{ github.run_attempt }}"

        # Job-level env var must define DRILL_PG_PASSWORD with the run-scoped expression
        assert "DRILL_PG_PASSWORD:" in text, (
            "DRILL_PG_PASSWORD job env var is missing from the workflow.\n"
            "Add under job-level `env:` block."
        )
        assert f"DRILL_PG_PASSWORD: {run_scoped_expr}" in text, (
            f"DRILL_PG_PASSWORD must be set to the run-scoped expression:\n"
            f"  DRILL_PG_PASSWORD: {run_scoped_expr}"
        )

        # Postgres service must use the same run-scoped expression directly
        # (env context is not available inside services.env)
        assert f"POSTGRES_PASSWORD: {run_scoped_expr}" in text, (
            f"Postgres service POSTGRES_PASSWORD must be set to the run-scoped expression:\n"
            f"  POSTGRES_PASSWORD: {run_scoped_expr}\n"
            "Do NOT use ${{ env.DRILL_PG_PASSWORD }} here (env context unavailable in services)."
        )

    # ── Test D ────────────────────────────────────────────────────────────────

    def test_d_local_db_url_uses_env_var(self) -> None:
        """D: LOCAL_DB_URL must reference the env var, not an inline password."""
        text = _read_workflow()

        # At least one of the two reference forms must appear adjacent to LOCAL_DB_URL
        has_brace_form = "${DRILL_PG_PASSWORD}" in text
        has_plain_form = "$DRILL_PG_PASSWORD" in text

        assert has_brace_form or has_plain_form, (
            "LOCAL_DB_URL construction must reference DRILL_PG_PASSWORD via env var.\n"
            "Expected one of:\n"
            "  postgresql://postgres:${DRILL_PG_PASSWORD}@...\n"
            "  postgresql://postgres:$DRILL_PG_PASSWORD@..."
        )

    # ── Test E ────────────────────────────────────────────────────────────────

    def test_e_ephemeral_password_is_masked(self) -> None:
        """E: Ephemeral password must be masked via ::add-mask:: before it is used."""
        text = _read_workflow()

        assert "::add-mask::" in text, (
            "::add-mask:: directive is missing from the workflow.\n"
            "Add a step: echo '::add-mask::$DRILL_PG_PASSWORD'"
        )
        assert "DRILL_PG_PASSWORD" in text, (
            "DRILL_PG_PASSWORD not referenced alongside ::add-mask::.\n"
            "Ensure the mask step targets the ephemeral password env var."
        )
