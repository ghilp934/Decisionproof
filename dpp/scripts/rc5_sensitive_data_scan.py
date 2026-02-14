#!/usr/bin/env python3
"""RC-5 Sensitive Data Scan (Forensic Patch - Fail-Closed with S1/S2 Rules).

CRITICAL SAFETY RULES:
- S1) LOG REDACTION: NEVER print secret literals. Only print:
  - file path, secret type, fingerprint (sha256[:12]), and "REDACTED"
- S2) TEST FIXTURE WHITELIST:
  - Exclude tests/** from content scanning
  - sk_test_* / dummy_* patterns => WARNING (not FAIL)
  - Forbidden file types in tests/ => WARNING

Exit codes:
  0 = PASS (no FAIL-class findings)
  1 = FAIL (FAIL-class findings exist)
  2 = ERROR (env/tooling issue OR missing scope)
"""

import hashlib
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Set, Tuple


# Forbidden file types (FAIL outside tests/, WARNING inside tests/)
FORBIDDEN_FILE_TYPES = {
    ".env", ".pem", ".key", ".p12", ".pfx",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    "credentials.json", "secrets.json",
}

# Legitimate certificate bundles (whitelist)
LEGITIMATE_CERT_PATTERNS = {
    "/usr/local/lib/python3.12/site-packages/pip/_vendor/certifi/cacert.pem",
    "/usr/local/lib/python3.12/site-packages/botocore/cacert.pem",
    "/usr/local/lib/python3.11/site-packages/pip/_vendor/certifi/cacert.pem",
    "/usr/local/lib/python3.11/site-packages/botocore/cacert.pem",
}

# Secret patterns (content-based detection)
SECRET_PATTERNS = [
    (r"(?i)(aws_access_key_id|aws_secret_access_key)\s*=\s*[A-Z0-9]{16,}", "AWS Credential"),
    (r"(?i)(AKIA[0-9A-Z]{16})", "AWS Access Key"),
    (r"(?i)(ghp_[a-zA-Z0-9]{36})", "GitHub Personal Access Token"),
    (r"(?i)(xox[baprs]-[0-9a-zA-Z-]{10,})", "Slack Token"),
    (r"(?i)(sk_live_[a-zA-Z0-9]{24,})", "Stripe Live Key"),
    (r"-----BEGIN (RSA |DSA |EC )?PRIVATE KEY-----", "Private Key Block"),
]


def compute_fingerprint(value: str) -> str:
    """Compute SHA256 fingerprint (first 12 chars) - S1 REDACTION.

    Args:
        value: Secret value

    Returns:
        First 12 chars of SHA256 hex digest
    """
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def is_test_path(path: str) -> bool:
    """Check if path is under tests/ directory - S2 WHITELIST.

    Args:
        path: File path

    Returns:
        True if path contains /tests/ or tests/
    """
    parts = Path(path).parts
    return "tests" in parts


def is_whitelisted_value(value: str) -> bool:
    """Check if value matches whitelist patterns - S2 WHITELIST.

    Args:
        value: Detected secret value

    Returns:
        True if value starts with sk_test_ or dummy_
    """
    return value.startswith("sk_test_") or value.startswith("dummy_")


def check_docker_availability() -> Tuple[bool, str, str]:
    """Check if docker is available and accessible.

    Returns:
        (available, version_info, error_msg)
    """
    try:
        result = subprocess.run(
            ["docker", "version", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        version_data = json.loads(result.stdout)
        client_version = version_data.get("Client", {}).get("Version", "unknown")
        server_version = version_data.get("Server", {}).get("Version", "unknown")
        version_info = f"Client={client_version}, Server={server_version}"
        return True, version_info, ""
    except subprocess.CalledProcessError as e:
        return False, "", f"Docker command failed: {e.stderr}"
    except subprocess.TimeoutExpired:
        return False, "", "Docker command timed out (daemon unreachable?)"
    except FileNotFoundError:
        return False, "", "Docker CLI not found"
    except Exception as e:
        return False, "", f"Docker check failed: {e}"


def scan_file_content(file_path: Path) -> List[Tuple[str, str, str]]:
    """Scan file content for secret patterns - S1 REDACTION.

    Args:
        file_path: Path to file

    Returns:
        List of (secret_type, fingerprint, class) tuples
    """
    findings = []

    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        for pattern, secret_type in SECRET_PATTERNS:
            matches = re.findall(pattern, content)
            for match in matches:
                # Extract matched value (handle tuple results from groups)
                if isinstance(match, tuple):
                    value = match[0] if match[0] else match[1]
                else:
                    value = match

                # S1 REDACTION: Never print value, only fingerprint
                fingerprint = compute_fingerprint(value)

                # S2 WHITELIST: Check for test value patterns
                if is_whitelisted_value(value):
                    finding_class = "WARN"
                else:
                    finding_class = "FAIL"

                findings.append((secret_type, fingerprint, finding_class))

    except Exception:
        pass  # Skip unreadable files

    return findings


def scan_repo_files() -> Tuple[List[Dict], int, int]:
    """Scan tracked repo files for secrets - S1/S2 compliant.

    Returns:
        (findings, scanned_count, excluded_count)

    Raises:
        RuntimeError: If git fails or no files found
    """
    findings = []
    repo_root = Path(__file__).parent.parent

    # Get git-tracked files
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        tracked_files = result.stdout.splitlines()
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Git ls-files failed: {e.stderr}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Git ls-files timed out")

    if not tracked_files:
        raise RuntimeError("Git ls-files returned 0 files (empty repo?)")

    scanned_count = 0
    excluded_count = 0

    # Scan each file
    for rel_path in tracked_files:
        file_path = repo_root / rel_path
        if not file_path.exists() or not file_path.is_file():
            continue

        is_test = is_test_path(rel_path)

        # Check forbidden file types (S2: tests/ => WARNING)
        basename = file_path.name.lower()
        suffix = file_path.suffix.lower()

        is_forbidden_type = (
            basename in FORBIDDEN_FILE_TYPES or
            suffix in FORBIDDEN_FILE_TYPES
        )

        if is_forbidden_type:
            finding_class = "WARN" if is_test else "FAIL"
            findings.append({
                "path": rel_path,
                "type": "Forbidden File Type",
                "fingerprint": "N/A",
                "class": finding_class,
                "remediation": "Remove or move to tests/" if finding_class == "FAIL" else "Test fixture (OK)",
            })
            scanned_count += 1
            continue

        # Content scan (S2: skip tests/ for content patterns)
        if not is_test:
            content_findings = scan_file_content(file_path)
            for secret_type, fingerprint, finding_class in content_findings:
                findings.append({
                    "path": rel_path,
                    "type": secret_type,
                    "fingerprint": fingerprint,
                    "class": finding_class,
                    "remediation": "Rotate secret and remove from repo" if finding_class == "FAIL" else "Test value (OK)",
                })
            scanned_count += 1
        else:
            excluded_count += 1

    return findings, scanned_count, excluded_count


def verify_image_exists(image_tag: str) -> Tuple[bool, str, str]:
    """Verify docker image exists and get its ID.

    Args:
        image_tag: Docker image tag

    Returns:
        (exists, image_id, error_msg)
    """
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image_tag, "--format", "{{.Id}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        image_id = result.stdout.strip()
        return True, image_id, ""
    except subprocess.CalledProcessError as e:
        return False, "", f"Image not found: {e.stderr.strip()}"
    except Exception as e:
        return False, "", str(e)


def scan_docker_image(image_tag: str) -> Tuple[List[Dict], int]:
    """Scan docker image for secrets using DUAL SCAN approach.

    DUAL SCAN STRATEGY:
    1. Tar listing (forensic evidence - counts tar entries)
    2. Filesystem scan (actual runtime scan - detects real files)

    Args:
        image_tag: Docker image tag

    Returns:
        (findings, tar_entries_count)

    Raises:
        RuntimeError: If scan fails or produces 0 entries
    """
    findings = []

    # SCAN 1: Tar listing for forensic evidence
    try:
        save_process = subprocess.Popen(
            ["docker", "save", image_tag],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        tar_process = subprocess.Popen(
            ["tar", "tf", "-"],
            stdin=save_process.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        save_process.stdout.close()
        tar_output, tar_error = tar_process.communicate(timeout=60)
        save_process.wait(timeout=5)

        if tar_process.returncode != 0:
            raise RuntimeError(f"Tar listing failed: {tar_error}")

        entries = tar_output.splitlines()
        tar_entries_count = len(entries)

        if tar_entries_count == 0:
            raise RuntimeError(f"Docker save produced 0 tar entries for {image_tag}")

        # Scan tar entries for forbidden patterns in archive structure
        for line in entries:
            basename = Path(line).name.lower()
            suffix = Path(line).suffix.lower()

            is_forbidden = (
                basename in FORBIDDEN_FILE_TYPES or
                suffix in FORBIDDEN_FILE_TYPES
            )

            if is_forbidden:
                findings.append({
                    "path": f"{image_tag}::{line}",
                    "type": "Forbidden File Type in Image (tar archive)",
                    "fingerprint": "N/A",
                    "class": "FAIL",
                    "remediation": "Rebuild image with .dockerignore",
                })

    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Docker save/tar timed out for {image_tag}")
    except Exception as e:
        raise RuntimeError(f"Docker tar listing failed for {image_tag}: {e}")

    # SCAN 2: Actual filesystem scan using docker run + find
    # This catches files created at runtime (like TRAP 2)
    try:
        # Find all files in the container filesystem
        result = subprocess.run(
            ["docker", "run", "--rm", image_tag, "find", "/", "-type", "f"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            # Non-zero exit is acceptable (find may hit permission errors)
            # We still process whatever output we got
            pass

        filesystem_files = result.stdout.splitlines()

        # Scan filesystem files for forbidden patterns
        for file_path in filesystem_files:
            file_lower = file_path.lower()
            basename = Path(file_path).name.lower()
            suffix = Path(file_path).suffix.lower()

            # Skip legitimate certificate bundles
            if file_path in LEGITIMATE_CERT_PATTERNS:
                continue

            # Check forbidden file types
            is_forbidden = (
                basename in FORBIDDEN_FILE_TYPES or
                suffix in FORBIDDEN_FILE_TYPES
            )

            if is_forbidden:
                findings.append({
                    "path": f"{image_tag}::{file_path}",
                    "type": "Forbidden File Type in Image (filesystem)",
                    "fingerprint": "N/A",
                    "class": "FAIL",
                    "remediation": "Rebuild image with .dockerignore",
                })

    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Docker filesystem scan timed out for {image_tag}")
    except Exception as e:
        raise RuntimeError(f"Docker filesystem scan failed for {image_tag}: {e}")

    return findings, tar_entries_count


def generate_scan_report(
    repo_findings: List[Dict],
    docker_findings: List[Dict],
    scope_evidence: Dict,
    timings: Dict,
) -> None:
    """Generate RC5_SENSITIVE_DATA_SCAN_REPORT.md with forensic scope evidence.

    Args:
        repo_findings: Findings from repo scan
        docker_findings: Findings from docker scan
        scope_evidence: Scope metadata
        timings: Timing data
    """
    repo_root = Path(__file__).parent.parent
    report_path = repo_root / "docs" / "rc" / "rc5" / "RC5_SENSITIVE_DATA_SCAN_REPORT.md"

    commit_hash = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    ).stdout.strip()[:8]

    timestamp = datetime.now(timezone.utc).isoformat()

    all_findings = repo_findings + docker_findings
    fail_findings = [f for f in all_findings if f["class"] == "FAIL"]
    warn_findings = [f for f in all_findings if f["class"] == "WARN"]

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# RC-5 Sensitive Data Scan Report\n\n")
        f.write(f"**Generated At:** {timestamp}  \n")
        f.write(f"**Commit:** `{commit_hash}`  \n")
        f.write("\n---\n\n")

        # FORENSIC SCOPE EVIDENCE (MANDATORY)
        f.write("## Forensic Scope Evidence\n\n")
        f.write("### Repository Scan\n")
        f.write(f"- **Scanned Files Count:** {scope_evidence['repo_scanned_files']}  \n")
        f.write(f"- **Excluded Files Count (tests/):** {scope_evidence['repo_excluded_files']}  \n")
        f.write(f"- **Root Path:** `{scope_evidence['repo_root']}`  \n")
        f.write("\n")

        f.write("### Docker Scan\n")
        f.write(f"- **Docker Available:** {scope_evidence['docker_available']}  \n")
        f.write(f"- **Docker Version:** {scope_evidence['docker_version']}  \n")
        f.write(f"- **Expected Images:** {', '.join(f'`{img}`' for img in scope_evidence['expected_images'])}  \n")
        f.write("\n**Scanned Images:**\n")
        for img_info in scope_evidence['scanned_images']:
            f.write(f"- **{img_info['tag']}**  \n")
            f.write(f"  - Image ID: `{img_info['image_id']}`  \n")
            f.write(f"  - Tar Entries: {img_info['tar_entries_count']}  \n")
        f.write("\n")

        f.write("### Timing\n")
        f.write(f"- **Repo Scan Duration:** {timings['repo_scan_s']:.2f}s  \n")
        f.write(f"- **Docker Build Duration:** {timings['docker_build_s']:.2f}s  \n")
        f.write(f"- **Docker Scan Duration:** {timings['docker_scan_s']:.2f}s  \n")
        f.write(f"- **Total Duration:** {timings['total_s']:.2f}s  \n")
        f.write("\n")

        # Summary
        f.write("## Summary\n\n")
        status = "[OK] PASS" if len(fail_findings) == 0 else "[FAIL] FAIL"
        f.write(f"**Status:** {status}  \n")
        f.write(f"**FAIL Findings:** {len(fail_findings)}  \n")
        f.write(f"**WARN Findings:** {len(warn_findings)}  \n")
        f.write("\n")

        # FAIL Findings
        if fail_findings:
            f.write("## [FAIL] FAIL Findings\n\n")
            f.write("**Action Required:** Rotate/revoke secrets and remove from repository.\\n\\n")
            f.write("| Path | Type | Fingerprint | Remediation |\n")
            f.write("|------|------|-------------|-------------|\n")
            for finding in fail_findings:
                f.write(f"| `{finding['path']}` | {finding['type']} | `{finding['fingerprint']}` | {finding['remediation']} |\n")
            f.write("\n")

        # WARN Findings
        if warn_findings:
            f.write("## [WARN] WARN Findings\n\n")
            f.write("**Note:** These are test fixtures or whitelisted patterns (non-failing).\n\n")
            f.write("| Path | Type | Fingerprint | Note |\n")
            f.write("|------|------|-------------|------|\n")
            for finding in warn_findings:
                f.write(f"| `{finding['path']}` | {finding['type']} | `{finding['fingerprint']}` | {finding['remediation']} |\n")
            f.write("\n")

        if not fail_findings and not warn_findings:
            f.write("## [OK] No Findings\n\n")
            f.write("No secrets or forbidden artifacts detected.\n\n")

        f.write("---\n\n")
        f.write("*Generated by RC-5 Sensitive Data Scan (Forensic Patch - S1 Redaction + S2 Whitelist)*\n")

    print(f"[OK] Generated: {report_path}")


def main() -> int:
    """Execute sensitive data scan with fail-closed policy.

    Returns:
        Exit code (0=PASS, 1=FAIL, 2=ERROR)
    """
    print("=" * 80)
    print("RC-5 SENSITIVE DATA SCAN (FORENSIC PATCH)")
    print("=" * 80)
    print()

    start_time = time.time()
    timings = {}
    scope_evidence = {}

    # PREFLIGHT: Docker availability check (REQUIRED)
    print("[PREFLIGHT] Checking docker availability...")
    docker_available, docker_version, docker_error = check_docker_availability()

    scope_evidence['docker_available'] = docker_available
    scope_evidence['docker_version'] = docker_version if docker_available else f"ERROR: {docker_error}"

    if not docker_available:
        print(f"[FAIL] ERROR: Docker not available: {docker_error}")
        print("RC-5 requires docker for image scanning. BLOCKING.")

        # Generate error report
        scope_evidence['repo_scanned_files'] = 0
        scope_evidence['repo_excluded_files'] = 0
        scope_evidence['repo_root'] = str(Path(__file__).parent.parent)
        scope_evidence['expected_images'] = []
        scope_evidence['scanned_images'] = []
        timings = {'repo_scan_s': 0, 'docker_build_s': 0, 'docker_scan_s': 0, 'total_s': 0}

        generate_scan_report([], [], scope_evidence, timings)
        return 2

    print(f"[OK] Docker available: {docker_version}")
    print()

    # 1) Scan repo
    print("[1/3] Scanning repository files...")
    repo_scan_start = time.time()

    try:
        repo_findings, scanned_count, excluded_count = scan_repo_files()
        repo_scan_duration = time.time() - repo_scan_start

        scope_evidence['repo_scanned_files'] = scanned_count
        scope_evidence['repo_excluded_files'] = excluded_count
        scope_evidence['repo_root'] = str(Path(__file__).parent.parent)

        repo_fail = [f for f in repo_findings if f["class"] == "FAIL"]
        repo_warn = [f for f in repo_findings if f["class"] == "WARN"]

        print(f"  Scanned {scanned_count} files (excluded {excluded_count} from tests/)")
        print(f"  FAIL findings: {len(repo_fail)}")
        print(f"  WARN findings: {len(repo_warn)}")

    except RuntimeError as e:
        print(f"[FAIL] ERROR: Repo scan failed: {e}")
        return 2

    print()
    timings['repo_scan_s'] = repo_scan_duration

    # 2) Verify docker images
    print("[2/3] Verifying docker images...")
    expected_images = [
        "decisionproof-api:rc5",
        "decisionproof-worker:rc5",
        "decisionproof-reaper:rc5",
    ]

    scope_evidence['expected_images'] = expected_images
    scope_evidence['scanned_images'] = []

    docker_build_start = time.time()

    for image_tag in expected_images:
        exists, image_id, error_msg = verify_image_exists(image_tag)

        if not exists:
            print(f"[FAIL] ERROR: Image {image_tag} not found: {error_msg}")
            print("RC-5 requires all images to be built before scanning.")
            timings['docker_build_s'] = time.time() - docker_build_start
            timings['docker_scan_s'] = 0
            timings['total_s'] = time.time() - start_time
            generate_scan_report(repo_findings, [], scope_evidence, timings)
            return 2

        print(f"  [OK] {image_tag} -> {image_id[:12]}")
        scope_evidence['scanned_images'].append({
            'tag': image_tag,
            'image_id': image_id,
            'tar_entries_count': 0,
        })

    timings['docker_build_s'] = time.time() - docker_build_start
    print()

    # 3) Scan docker images
    print("[3/3] Scanning docker images...")
    docker_scan_start = time.time()

    docker_findings = []

    for idx, image_tag in enumerate(expected_images):
        try:
            print(f"  Scanning {image_tag}...")
            image_findings, tar_entries_count = scan_docker_image(image_tag)
            docker_findings.extend(image_findings)

            scope_evidence['scanned_images'][idx]['tar_entries_count'] = tar_entries_count

            print(f"    -> {tar_entries_count} tar entries scanned, {len(image_findings)} findings")

        except RuntimeError as e:
            print(f"[FAIL] ERROR: Docker scan failed for {image_tag}: {e}")
            timings['docker_scan_s'] = time.time() - docker_scan_start
            timings['total_s'] = time.time() - start_time
            generate_scan_report(repo_findings, docker_findings, scope_evidence, timings)
            return 2

    timings['docker_scan_s'] = time.time() - docker_scan_start

    docker_fail = [f for f in docker_findings if f["class"] == "FAIL"]
    print(f"  FAIL findings: {len(docker_fail)}")

    print()

    # Generate report
    timings['total_s'] = time.time() - start_time

    all_fail = [f for f in (repo_findings + docker_findings) if f["class"] == "FAIL"]

    print("[SCOPE EVIDENCE]")
    print(f"  Repo: {scope_evidence['repo_scanned_files']} files scanned (excluded {scope_evidence['repo_excluded_files']} from tests/)")
    print(f"  Docker: {len(scope_evidence['scanned_images'])} images scanned")
    for img_info in scope_evidence['scanned_images']:
        print(f"    - {img_info['tag']}: {img_info['tar_entries_count']} tar entries")
    print(f"  Total duration: {timings['total_s']:.2f}s")
    print()

    if all_fail:
        print("[FAIL] Found secrets or forbidden artifacts:")
        for finding in all_fail[:5]:
            # S1 REDACTION: Never print secret value
            print(f"  - {finding['path']}: {finding['type']} (fingerprint: {finding['fingerprint']})")
        if len(all_fail) > 5:
            print(f"  ... and {len(all_fail) - 5} more (see report)")

    generate_scan_report(repo_findings, docker_findings, scope_evidence, timings)

    print()
    print("=" * 80)

    if all_fail:
        print(f"RC-5 SENSITIVE DATA SCAN: FAIL ({len(all_fail)} findings)")
        print("=" * 80)
        return 1
    else:
        print("RC-5 SENSITIVE DATA SCAN: PASS")
        print("=" * 80)
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[WARN] Interrupted by user", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"[FAIL] ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(2)
