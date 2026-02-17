#!/usr/bin/env python3
"""RC-5 Release Hygiene Check (Forensic Patch - Fail-Closed).

Scans repo and docker images for forbidden artifacts.
Exit codes:
  0 = PASS (no forbidden artifacts)
  1 = FAIL (forbidden artifacts found)
  2 = ERROR (env/tooling issue OR missing scope)
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Set, Tuple

# Forbidden patterns (strict)
FORBIDDEN_PATTERNS = [
    "__pycache__", ".pyc", ".coverage", ".pytest_cache",
    ".log", ".backup", ".bak", ".swp",
]

# Forbidden file basenames (case-insensitive)
FORBIDDEN_BASENAMES = {
    ".env",
    "credentials.json",
    "secrets.json",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
}

# Forbidden extensions
FORBIDDEN_EXTENSIONS = {".env", ".pem", ".key", ".p12", ".pfx"}

# R1: AWS Access Key ID pattern (AKIA*, ASIA*)
AWS_KEY_ID_PATTERN = re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")

# R1: File extensions to scan for AWS Key IDs (text files only)
AWS_KEY_SCAN_EXTENSIONS = {
    ".py", ".md", ".txt", ".yml", ".yaml", ".json", ".toml", ".env", ".ini",
    ".sh", ".bash", ".js", ".ts", ".jsx", ".tsx", ".html", ".xml", ".csv",
}

# R1: Max file size for AWS Key ID scan (500KB)
AWS_KEY_SCAN_MAX_SIZE = 500 * 1024


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


def scan_repo_tree() -> Tuple[List[str], int, int]:
    """Scan repo working tree for forbidden artifacts + AWS Key IDs (R1).

    Returns:
        (violations, scanned_count, excluded_count)

    Raises:
        RuntimeError: If git fails or no files found
    """
    violations = []
    repo_root = Path(__file__).parent.parent

    # R1: Toggle for AWS Key ID scanning
    scan_aws_key_ids = os.getenv("RC5_SCAN_AWS_KEY_IDS", "1") == "1"

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

    scanned_count = len(tracked_files)
    excluded_count = 0  # Currently we scan all tracked files

    # Check each tracked file
    for file_path in tracked_files:
        file_lower = file_path.lower()

        # Check against forbidden patterns
        if any(pattern in file_lower for pattern in FORBIDDEN_PATTERNS):
            violations.append(file_path)
            continue

        # Check forbidden basenames
        basename = Path(file_path).name.lower()
        if basename in {b.lower() for b in FORBIDDEN_BASENAMES}:
            violations.append(file_path)
            continue

        # Check forbidden extensions
        suffix = Path(file_path).suffix.lower()
        if suffix in FORBIDDEN_EXTENSIONS:
            violations.append(file_path)
            continue

        # R1: Scan text files for AWS Access Key IDs
        if scan_aws_key_ids and suffix in AWS_KEY_SCAN_EXTENSIONS:
            full_path = repo_root / file_path

            try:
                # Skip files larger than max size
                if full_path.stat().st_size > AWS_KEY_SCAN_MAX_SIZE:
                    continue

                # Read file content (text mode, skip binary)
                with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()

                # Search for AWS Key ID pattern
                if AWS_KEY_ID_PATTERN.search(content):
                    violations.append(f"{file_path} [AWS_KEY_ID]")

            except (OSError, UnicodeDecodeError):
                # Skip unreadable files
                pass

    return sorted(set(violations)), scanned_count, excluded_count


def verify_image_exists(image_tag: str) -> Tuple[bool, str, str]:
    """Verify docker image exists and get its ID.

    Args:
        image_tag: Docker image tag (e.g., "decisionproof-api:rc5")

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


def scan_docker_image(image_tag: str) -> Tuple[List[str], int]:
    """Scan docker image for forbidden artifacts using tar listing.

    Args:
        image_tag: Docker image tag

    Returns:
        (violations, tar_entries_count)

    Raises:
        RuntimeError: If scan fails or produces 0 entries
    """
    violations = []

    try:
        # Use docker save + tar to list all files in image
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

        save_process.stdout.close()  # Allow save_process to receive SIGPIPE
        tar_output, tar_error = tar_process.communicate(timeout=60)
        save_process.wait(timeout=5)

        if tar_process.returncode != 0:
            raise RuntimeError(f"Tar listing failed: {tar_error}")

        entries = tar_output.splitlines()
        tar_entries_count = len(entries)

        if tar_entries_count == 0:
            raise RuntimeError(f"Docker save produced 0 tar entries for {image_tag}")

        # Scan each entry for forbidden patterns
        for entry in entries:
            entry_lower = entry.lower()

            # Check for forbidden patterns
            if any(pattern in entry_lower for pattern in FORBIDDEN_PATTERNS):
                violations.append(f"{image_tag}::{entry}")
                continue

            if any(ext in entry_lower for ext in [".env", ".pem", ".key", "id_rsa", ".p12", ".pfx"]):
                violations.append(f"{image_tag}::{entry}")
                continue

            # Check forbidden basenames
            basename = Path(entry).name.lower()
            if basename in {b.lower() for b in FORBIDDEN_BASENAMES}:
                violations.append(f"{image_tag}::{entry}")

        return sorted(set(violations)), tar_entries_count

    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Docker save/tar timed out for {image_tag}")
    except Exception as e:
        raise RuntimeError(f"Docker scan failed for {image_tag}: {e}")


def generate_report(
    repo_violations: List[str],
    docker_violations: List[str],
    scope_evidence: Dict,
    timings: Dict,
) -> None:
    """Generate RC5_RELEASE_HYGIENE_REPORT.md with forensic scope evidence.

    Args:
        repo_violations: Violations from repo scan
        docker_violations: Violations from docker scan
        scope_evidence: Scope metadata (files scanned, images scanned, etc.)
        timings: Timing data for each stage
    """
    repo_root = Path(__file__).parent.parent
    report_path = repo_root / "docs" / "rc" / "rc5" / "RC5_RELEASE_HYGIENE_REPORT.md"

    commit_hash = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    ).stdout.strip()[:8]

    timestamp = datetime.now(timezone.utc).isoformat()

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# RC-5 Release Hygiene Report\n\n")
        f.write(f"**Generated At:** {timestamp}  \n")
        f.write(f"**Commit:** `{commit_hash}`  \n")
        f.write("\n---\n\n")

        # FORENSIC SCOPE EVIDENCE (MANDATORY)
        f.write("## Forensic Scope Evidence\n\n")
        f.write("### Repository Scan\n")
        f.write(f"- **Scanned Files Count:** {scope_evidence['repo_scanned_files']}  \n")
        f.write(f"- **Excluded Files Count:** {scope_evidence['repo_excluded_files']}  \n")
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
        total_violations = len(repo_violations) + len(docker_violations)
        status = "[OK] PASS" if total_violations == 0 else "[FAIL] FAIL"
        f.write(f"**Status:** {status}  \n")
        f.write(f"**Repo Violations:** {len(repo_violations)}  \n")
        f.write(f"**Docker Violations:** {len(docker_violations)}  \n")
        f.write("\n")

        # Violations
        if repo_violations:
            f.write("## [FAIL] Repository Violations\n\n")
            for path in repo_violations[:50]:
                f.write(f"- `{path}`\n")
            if len(repo_violations) > 50:
                f.write(f"\n... and {len(repo_violations) - 50} more\n\n")

        if docker_violations:
            f.write("## [FAIL] Docker Image Violations\n\n")
            for path in docker_violations:
                f.write(f"- `{path}`\n")
            f.write("\n")

        if not repo_violations and not docker_violations:
            f.write("## [OK] No Violations\n\n")
            f.write("No forbidden artifacts detected in repo or docker images.\n\n")

        f.write("---\n\n")
        f.write("*Generated by RC-5 Release Hygiene Check (Forensic Patch)*\n")

    print(f"[OK] Generated: {report_path}")


def main() -> int:
    """Execute release hygiene check with fail-closed policy.

    Returns:
        Exit code (0=PASS, 1=FAIL, 2=ERROR)
    """
    print("=" * 80)
    print("RC-5 RELEASE HYGIENE CHECK (FORENSIC PATCH)")
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

        generate_report([], [], scope_evidence, timings)
        return 2

    print(f"[OK] Docker available: {docker_version}")
    print()

    # 1) Scan repo tree
    print("[1/3] Scanning repo working tree...")
    repo_scan_start = time.time()

    try:
        repo_violations, scanned_count, excluded_count = scan_repo_tree()
        repo_scan_duration = time.time() - repo_scan_start

        scope_evidence['repo_scanned_files'] = scanned_count
        scope_evidence['repo_excluded_files'] = excluded_count
        scope_evidence['repo_root'] = str(Path(__file__).parent.parent)

        print(f"  Scanned {scanned_count} files (excluded {excluded_count})")

        if repo_violations:
            print(f"[FAIL] Found {len(repo_violations)} forbidden artifact(s) in repo:")
            for path in repo_violations[:10]:
                print(f"  - {path}")
            if len(repo_violations) > 10:
                print(f"  ... and {len(repo_violations) - 10} more")
        else:
            print("[OK] No forbidden artifacts in repo")

    except RuntimeError as e:
        print(f"[FAIL] ERROR: Repo scan failed: {e}")
        return 2

    print()
    timings['repo_scan_s'] = repo_scan_duration

    # 2) Verify docker images exist
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
            generate_report(repo_violations, [], scope_evidence, timings)
            return 2

        print(f"  [OK] {image_tag} -> {image_id[:12]}")
        scope_evidence['scanned_images'].append({
            'tag': image_tag,
            'image_id': image_id,
            'tar_entries_count': 0,  # Will be filled during scan
        })

    timings['docker_build_s'] = time.time() - docker_build_start
    print()

    # 3) Scan docker images
    print("[3/3] Scanning docker images...")
    docker_scan_start = time.time()

    docker_violations = []

    for idx, image_tag in enumerate(expected_images):
        try:
            print(f"  Scanning {image_tag}...")
            image_violations, tar_entries_count = scan_docker_image(image_tag)
            docker_violations.extend(image_violations)

            # Update scope evidence with tar entry count
            scope_evidence['scanned_images'][idx]['tar_entries_count'] = tar_entries_count

            print(f"    -> {tar_entries_count} tar entries scanned, {len(image_violations)} violations")

        except RuntimeError as e:
            print(f"[FAIL] ERROR: Docker scan failed for {image_tag}: {e}")
            timings['docker_scan_s'] = time.time() - docker_scan_start
            timings['total_s'] = time.time() - start_time
            generate_report(repo_violations, docker_violations, scope_evidence, timings)
            return 2

    timings['docker_scan_s'] = time.time() - docker_scan_start

    if docker_violations:
        print(f"[FAIL] Found {len(docker_violations)} forbidden artifact(s) in docker images:")
        for path in docker_violations[:10]:
            print(f"  - {path}")
        if len(docker_violations) > 10:
            print(f"  ... and {len(docker_violations) - 10} more")
    else:
        print("[OK] No forbidden artifacts in docker images")

    print()

    # Generate report
    timings['total_s'] = time.time() - start_time

    print("[SCOPE EVIDENCE]")
    print(f"  Repo: {scope_evidence['repo_scanned_files']} files scanned")
    print(f"  Docker: {len(scope_evidence['scanned_images'])} images scanned")
    for img_info in scope_evidence['scanned_images']:
        print(f"    - {img_info['tag']}: {img_info['tar_entries_count']} tar entries")
    print(f"  Total duration: {timings['total_s']:.2f}s")
    print()

    generate_report(repo_violations, docker_violations, scope_evidence, timings)

    print()
    print("=" * 80)

    # Summary
    total_violations = len(repo_violations) + len(docker_violations)

    if total_violations > 0:
        print(f"RC-5 RELEASE HYGIENE: FAIL ({total_violations} violations)")
        print("=" * 80)
        return 1
    else:
        print("RC-5 RELEASE HYGIENE: PASS")
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
