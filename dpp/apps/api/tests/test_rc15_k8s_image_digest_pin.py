"""
RC-15: K8s Image Digest Pin Gate
=================================
Purpose:
  Pilot overlay의 3개 Deployment patch 파일이 모두 "@sha256:" digest 방식으로
  이미지를 고정하고 있는지 검사한다. tag-only(:0.4.2.2) 또는 :latest 는 FAIL.

Rationale:
  - tag는 mutable (ECR에서 덮어쓰기 가능) → 배포 드리프트 발생 위험
  - digest는 immutable → 동일 SHA 보장, 재배포 시 동일 이미지 보장
  - Option B (digest pin) 정책을 코드로 박제한다

Fast/File-read only — no I/O, no network, no sleep.
"""
import pathlib
import re

import pytest

# ─── 대상 파일 경로 ───────────────────────────────────────────────────────────
PILOT_OVERLAY = pathlib.Path(__file__).parents[3] / "k8s" / "overlays" / "pilot"

PATCH_FILES = {
    "dpp-api": PILOT_OVERLAY / "patch-api-deployment-pilot.yaml",
    "dpp-worker": PILOT_OVERLAY / "patch-worker-deployment-pilot.yaml",
    "dpp-reaper": PILOT_OVERLAY / "patch-reaper-deployment-pilot.yaml",
}

# ─── 헬퍼 ────────────────────────────────────────────────────────────────────

def _image_lines(path: pathlib.Path) -> list[str]:
    """파일에서 'image:' 가 포함된 라인(들)을 반환 (주석 제외)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [
        line
        for line in lines
        if re.match(r"^\s+image:\s+", line)  # 들여쓰기가 있는 image: 라인
    ]


# ─── 파라미터화 테스트 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("service,patch_path", list(PATCH_FILES.items()))
class TestRC15DigestPin:
    def test_patch_file_exists(self, service, patch_path):
        """대상 patch 파일이 실제로 존재해야 한다."""
        assert patch_path.exists(), (
            f"{service}: patch file not found at {patch_path}\n"
            "RC-15 FAIL: 파일 자체가 없으면 digest pin 상태를 검증할 수 없음."
        )

    def test_image_line_present(self, service, patch_path):
        """patch 파일에 'image:' 라인이 최소 1개 있어야 한다."""
        lines = _image_lines(patch_path)
        assert lines, (
            f"{service}: 'image:' 라인이 {patch_path}에 없음.\n"
            "RC-15 FAIL: image 필드가 없으면 digest pin이 적용되지 않음."
        )

    def test_image_uses_digest_pin(self, service, patch_path):
        """image 라인에 '@sha256:' 가 포함되어야 한다 (digest pin 필수)."""
        lines = _image_lines(patch_path)
        for line in lines:
            assert "@sha256:" in line, (
                f"{service}: image 라인이 digest pin 형식이 아님.\n"
                f"  현재: {line.strip()}\n"
                f"  기대: <repo>@sha256:<digest>\n"
                "RC-15 FAIL: tag-only 이미지는 mutable — digest로 pin 필요."
            )

    def test_image_no_latest_tag(self, service, patch_path):
        """':latest' 태그가 이미지 라인에 있으면 FAIL."""
        lines = _image_lines(patch_path)
        for line in lines:
            assert ":latest" not in line, (
                f"{service}: ':latest' tag 사용 감지.\n"
                f"  현재: {line.strip()}\n"
                "RC-15 FAIL: :latest 는 배포 드리프트의 주요 원인 — 절대 금지."
            )

    def test_image_no_mutable_tag(self, service, patch_path):
        """순수 tag(:0.4.2.2 등) 방식이 남아있으면 FAIL.
        digest pin(@sha256:)을 사용하는 경우, colon+tag 패턴이 없어야 한다.
        단, digest 앞의 repo 주소에 포트(:숫자)가 있는 경우는 허용."""
        lines = _image_lines(patch_path)
        # tag-only 패턴: ":<version>" 이 존재하면서 "@sha256:" 가 없는 경우
        for line in lines:
            if "@sha256:" in line:
                continue  # digest pin OK
            # tag-only 패턴 탐지: colon 뒤 영숫자+점이 있는 경우
            repo_part = line.split("image:")[-1].strip()
            tag_match = re.search(r":[a-zA-Z0-9][a-zA-Z0-9._-]*$", repo_part)
            if tag_match:
                pytest.fail(
                    f"{service}: mutable tag 사용 감지 (digest pin 없음).\n"
                    f"  현재: {line.strip()}\n"
                    f"  태그: {tag_match.group()}\n"
                    "RC-15 FAIL: tag를 digest pin(@sha256:)으로 교체할 것."
                )
