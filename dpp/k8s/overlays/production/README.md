# DPP Production Overlay — 배포 가이드

## 오버레이 구조

```
k8s/overlays/
├── production/          ← 이 파일 (ACK 미주입 — 안전 기본값)
└── production-ack1/     ← ACK=1 주입 전용 (체크리스트 완료 후)
```

두 오버레이는 동일한 `k8s/base`를 참조하며, ConfigMap/Deployment 패치만 다릅니다.

---

## ⚠️ ACK=1 주입 규칙 (필수)

> **ACK=1은 `ops/runbooks/db_backup_restore.md` 체크리스트 완료(리허설 포함) 후에만 적용**

체크리스트 미완료 상태에서 `DPP_ACK_SUPABASE_BACKUP_POLICY=1`을 임의로 주입하면,
백업/복구 미검증 상태로 production 운영이 이루어집니다. **절대 금지.**

---

## 적용 커맨드

### 1. 안전 모드 (기본 — ACK 미주입)
```bash
# ConfigMap + Deployment 은 production 값으로 갱신되나,
# DPP_ACK_SUPABASE_BACKUP_POLICY 는 주입되지 않아 dpp-api가 Fail-Fast 상태 유지.
kubectl apply -k k8s/overlays/production
```

### 2. ACK=1 모드 (체크리스트 완료 후)
```bash
# ─────────────────────────────────────────────────────────────────────────
# STOP: ops/runbooks/db_backup_restore.md 체크리스트를 모두 완료했습니까?
#   [ ] db_backup_pgdump.sh 테스트 완료
#   [ ] db_restore_verify.sh 복원 리허설 완료 (테스트 환경)
#   [ ] 백업 자동화(cron/CronJob) 구성 완료
#   [ ] 백업 보관 위치 확정 (S3 등)
#   [ ] 복원 담당자 공유 완료
# ─────────────────────────────────────────────────────────────────────────
kubectl apply -k k8s/overlays/production-ack1

# ConfigMap 변경은 환경변수로 주입된 경우 파드에 자동 반영되지 않음.
# 적용 후 반드시 롤아웃 재시작 실행:
kubectl rollout restart -n dpp-production deploy/dpp-api
kubectl rollout status -n dpp-production deploy/dpp-api --timeout=300s
```

---

## 파일 역할

| 파일 | 역할 |
|------|------|
| `kustomization.yaml` | base 참조, namespace=dpp-production, environment=production 레이블 |
| `patch-configmap-production.yaml` | SQS URL 확정 + NETWORK_RESTRICTIONS ACK (BACKUP_POLICY 미주입) |

---

## 검증 커맨드

```bash
# production 렌더 — DPP_ACK_SUPABASE_BACKUP_POLICY 없어야 함
kubectl kustomize k8s/overlays/production | grep -n DPP_ACK_SUPABASE_BACKUP_POLICY || echo "OK: not present"

# production-ack1 렌더 — 1회 매칭 있어야 함
kubectl kustomize k8s/overlays/production-ack1 | grep -n DPP_ACK_SUPABASE_BACKUP_POLICY

# pilot 렌더 — 절대 없어야 함
kubectl kustomize k8s/overlays/pilot | grep -n DPP_ACK_SUPABASE_BACKUP_POLICY && echo "FAIL: found in pilot!" || echo "OK: not in pilot"
```

---

## 참고

- Backup 체크리스트: `ops/runbooks/db_backup_restore.md`
- Supabase 네트워크 강화: `ops/runbooks/supabase_hardening.md`
- Pilot 오버레이: `k8s/overlays/pilot/README.md`
