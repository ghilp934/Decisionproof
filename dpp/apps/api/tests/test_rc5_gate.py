"""RC-5 Contract Gate (Forensic Patch): Release Hygiene + API Inventory + Secrets Scanning + RED TEAM TRAPS.

This test acts as a pytest gate that runs all RC-5 verification scripts.
PASS requires all three scripts to exit with code 0 AND red team traps to FAIL as expected.

RED TEAM TRAPS:
- Trap 1 (Repo): Creates secret_trap.pem, expects scanner to FAIL
- Trap 2 (Docker): Builds trap image with fake AWS key, expects scanner to FAIL
"""

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


def run_script(script_name: str) -> tuple[int, str, str]:
    """Run RC-5 script and capture output.

    Args:
        script_name: Name of script (e.g., "rc5_release_hygiene_check.py")

    Returns:
        (exit_code, stdout, stderr)
    """
    repo_root = Path(__file__).parent.parent.parent.parent
    script_path = repo_root / "scripts" / script_name

    result = subprocess.run(
        [sys.executable, str(script_path)],
        capture_output=True,
        text=True,
        cwd=repo_root,
        timeout=300,  # 5 minutes max
    )

    return result.returncode, result.stdout, result.stderr


class TestRC5Gate:
    """RC-5 Contract Gate (Forensic Patch): Release Hygiene + API Inventory + Secrets Scanning."""

    def test_rc5_release_hygiene(self):
        """RC-5.1: Release hygiene check (repo + docker images with forensic scope evidence)."""
        exit_code, stdout, stderr = run_script("rc5_release_hygiene_check.py")

        # Print output for visibility
        if stdout:
            print("\n" + stdout)
        if stderr:
            print("\n" + stderr, file=sys.stderr)

        # Verify scope evidence is present in stdout
        assert "Scanned" in stdout, "Missing scope evidence: scanned files count"
        assert "tar entries" in stdout, "Missing scope evidence: tar entries count"
        assert "Total duration:" in stdout, "Missing scope evidence: timing"

        assert exit_code == 0, "Release hygiene check failed"

    def test_rc5_api_inventory(self):
        """RC-5.2: API inventory check (OpenAPI drift + hidden endpoints)."""
        exit_code, stdout, stderr = run_script("rc5_api_inventory.py")

        # Print output for visibility
        if stdout:
            print("\n" + stdout)
        if stderr:
            print("\n" + stderr, file=sys.stderr)

        assert exit_code == 0, "API inventory check failed"

    def test_rc5_sensitive_data_scan(self):
        """RC-5.3: Sensitive data scan (secrets detection with S1/S2 rules + forensic scope evidence)."""
        exit_code, stdout, stderr = run_script("rc5_sensitive_data_scan.py")

        # Print output for visibility
        if stdout:
            print("\n" + stdout)
        if stderr:
            print("\n" + stderr, file=sys.stderr)

        # Verify scope evidence is present in stdout
        assert "Scanned" in stdout, "Missing scope evidence: scanned files count"
        assert "tar entries" in stdout, "Missing scope evidence: tar entries count"
        assert "Total duration:" in stdout, "Missing scope evidence: timing"

        assert exit_code == 0, "Sensitive data scan failed"

    def test_rc5_evidence_pack_exists(self):
        """RC-5.4: Verify all evidence pack files exist."""
        repo_root = Path(__file__).parent.parent.parent.parent
        evidence_dir = repo_root / "docs" / "rc" / "rc5"

        required_files = [
            "RC5_HIDDEN_ENDPOINT_ALLOWLIST.txt",
            "RC5_API_INVENTORY.md",
            "RC5_SENSITIVE_DATA_SCAN_REPORT.md",
            "RC5_RELEASE_HYGIENE_REPORT.md",
        ]

        for filename in required_files:
            file_path = evidence_dir / filename
            assert file_path.exists(), f"Missing evidence file: {filename}"

            # Verify scope evidence is present in report files
            if filename.endswith(".md") and "HYGIENE" in filename:
                content = file_path.read_text(encoding="utf-8")
                assert "Forensic Scope Evidence" in content, f"{filename} missing forensic scope section"
                assert "Scanned Files Count" in content, f"{filename} missing scanned files count"
                assert "Tar Entries" in content, f"{filename} missing tar entries count"
                assert "Timing" in content, f"{filename} missing timing section"

            if filename.endswith(".md") and "SENSITIVE" in filename:
                content = file_path.read_text(encoding="utf-8")
                assert "Forensic Scope Evidence" in content, f"{filename} missing forensic scope section"
                assert "Scanned Files Count" in content, f"{filename} missing scanned files count"


class TestRC5RedTeamTraps:
    """RED TEAM TRAPS: Prove scanners are actually working (fail-safe validation)."""

    def test_trap1_repo_contamination_detection(self):
        """TRAP 1: Create forbidden file (secret_trap.pem), expect release hygiene to FAIL."""
        repo_root = Path(__file__).parent.parent.parent.parent
        trap_file = repo_root / "secret_trap.pem"

        try:
            # Create trap file
            trap_file.write_text("FAKE CERTIFICATE TRAP\n", encoding="utf-8")

            # Add to git (make it tracked)
            subprocess.run(
                ["git", "add", str(trap_file)],
                cwd=repo_root,
                check=True,
                capture_output=True,
            )

            # Run release hygiene check - MUST FAIL
            exit_code, stdout, stderr = run_script("rc5_release_hygiene_check.py")

            # Print output for debugging
            print("\n[TRAP 1 OUTPUT]")
            print(stdout)
            if stderr:
                print(stderr, file=sys.stderr)

            # Verify trap was caught
            assert exit_code == 1, f"TRAP 1 FAILED: Scanner did not detect secret_trap.pem (exit code {exit_code})"
            assert "secret_trap.pem" in stdout, "TRAP 1 FAILED: secret_trap.pem not in violations"

            print("[TRAP 1 SUCCESS] Scanner detected repo contamination")

        finally:
            # Cleanup: unstage and remove trap file
            subprocess.run(
                ["git", "reset", "HEAD", str(trap_file)],
                cwd=repo_root,
                capture_output=True,
            )
            if trap_file.exists():
                trap_file.unlink()

    def test_trap2_docker_image_secret_detection(self):
        """TRAP 2: Build docker image with forbidden .pem file, expect secrets scan to FAIL."""
        repo_root = Path(__file__).parent.parent.parent.parent

        # Check if docker is available first
        docker_check = subprocess.run(
            ["docker", "version"],
            capture_output=True,
            timeout=10,
        )

        if docker_check.returncode != 0:
            pytest.skip("Docker not available, skipping trap 2")

        # Verify base image exists
        base_image = "decisionproof-api:rc5"
        inspect_result = subprocess.run(
            ["docker", "image", "inspect", base_image],
            capture_output=True,
        )

        if inspect_result.returncode != 0:
            pytest.skip(f"Base image {base_image} not found, skipping trap 2")

        trap_image = "decisionproof-api:rc5-trap"

        try:
            # Create temporary Dockerfile with trap
            with tempfile.TemporaryDirectory() as tmpdir:
                dockerfile_path = Path(tmpdir) / "Dockerfile"
                # Create forbidden .pem file in /tmp (will be detected by filesystem scan)
                dockerfile_content = f"""FROM {base_image}
RUN echo "FAKE CERTIFICATE TRAP FOR RC5 TESTING" > /tmp/secret_trap.pem
"""
                dockerfile_path.write_text(dockerfile_content, encoding="utf-8")

                # Build trap image
                print(f"\n[TRAP 2] Building trap image {trap_image}...")
                build_result = subprocess.run(
                    ["docker", "build", "-t", trap_image, "."],
                    cwd=tmpdir,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )

                if build_result.returncode != 0:
                    pytest.fail(f"TRAP 2 SETUP FAILED: Could not build trap image: {build_result.stderr}")

                print(f"[TRAP 2] Trap image built successfully")

            # Verify trap file exists in image
            verify_result = subprocess.run(
                ["docker", "run", "--rm", trap_image, "ls", "-la", "/tmp/secret_trap.pem"],
                capture_output=True,
                text=True,
            )

            if verify_result.returncode != 0:
                pytest.fail(f"TRAP 2 SETUP FAILED: Trap file not found in image: {verify_result.stderr}")

            print(f"[TRAP 2] Verified trap file exists: /tmp/secret_trap.pem")

            # Patch script to include trap image
            scan_script = repo_root / "scripts" / "rc5_sensitive_data_scan.py"
            original_content = scan_script.read_text(encoding="utf-8")

            try:
                # Replace expected_images list with version including trap
                patched_content = original_content.replace(
                    'expected_images = [\n        "decisionproof-api:rc5",\n        "decisionproof-worker:rc5",\n        "decisionproof-reaper:rc5",\n    ]',
                    f'expected_images = [\n        "decisionproof-api:rc5",\n        "decisionproof-worker:rc5",\n        "decisionproof-reaper:rc5",\n        "{trap_image}",\n    ]'
                )

                # Verify patch was applied
                if patched_content == original_content:
                    pytest.fail("TRAP 2 SETUP FAILED: Script patch did not apply (pattern not found)")

                scan_script.write_text(patched_content, encoding="utf-8")
                print(f"[TRAP 2] Script patched to include trap image")

                # Run sensitive data scan - MUST FAIL
                exit_code, stdout, stderr = run_script("rc5_sensitive_data_scan.py")

                # Print output for debugging
                print("\n[TRAP 2 OUTPUT]")
                print(stdout)
                if stderr:
                    print(stderr, file=sys.stderr)

                # Verify trap was caught
                assert exit_code == 1, f"TRAP 2 FAILED: Scanner did not detect forbidden .pem file in trap image (exit code {exit_code})\nStdout:\n{stdout}"
                assert "secret_trap.pem" in stdout, f"TRAP 2 FAILED: secret_trap.pem not in violations\nStdout:\n{stdout}"

                print("[TRAP 2 SUCCESS] Scanner detected docker image forbidden artifact (.pem file)")

            finally:
                # Restore original script
                scan_script.write_text(original_content, encoding="utf-8")
                print(f"[TRAP 2] Script restored")

        finally:
            # Cleanup: remove trap image
            subprocess.run(
                ["docker", "rmi", "-f", trap_image],
                capture_output=True,
            )
            print(f"[TRAP 2] Cleanup complete")
