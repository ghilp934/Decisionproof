# DPP Production-ACK1 Overlay

## ⛔ 적용 전 필수 확인

> **ACK=1은 `ops/runbooks/db_backup_restore.md` 체크리스트 완료(리허설 포함) 후에만 적용**

이 오버레이는 production 가드레일(`DPP_ACK_SUPABASE_BACKUP_POLICY=1`)을 해제합니다.
아래 체크리스트를 완료하지 않고 적용하면 **미검증 상태로 production 운영**이 이루어집니다.

### 체크리스트 (`ops/runbooks/db_backup_restore.md` 참조)

- [ ] `db_backup_pgdump.sh` 백업 생성 테스트 완료
- [ ] `db_restore_verify.sh` 복원 리허설 완료 (테스트 환경)
- [ ] 백업 자동화(cron/CronJob) 구성 완료
- [ ] 백업 보관 위치(S3 등) 확정
- [ ] 복원 담당자 문서 공유 완료
- [ ] `python scripts/supabase_preflight.py` 실행 → PASS 확인

---

## 적용 커맨드 (체크리스트 완료 후)

```bash
# Step 1: ACK=1 오버레이 적용
kubectl apply -k k8s/overlays/production-ack1

# Step 2: 롤아웃 재시작 (ConfigMap 환경변수는 파드 재시작 없이 반영 안 됨)
kubectl rollout restart -n dpp-production deploy/dpp-api

# Step 3: 롤아웃 완료 확인
kubectl rollout status -n dpp-production deploy/dpp-api --timeout=300s

# Step 4: 로그 확인 (CrashLoopBackOff 해소 여부)
kubectl logs -n dpp-production deploy/dpp-api --tail=50
```

---

## 파일 역할

| 파일 | 역할 |
|------|------|
| `kustomization.yaml` | `../production` 오버레이를 base로 상속 + ACK 패치 2개 연결 |
| `patch-configmap-production-ack1.yaml` | ConfigMap `dpp-config`에 `DPP_ACK_SUPABASE_BACKUP_POLICY: "1"` 키 추가 |
| `patch-api-deployment-production-ack1.yaml` | `dpp-api` Deployment env에 ConfigMap 키 참조 추가 (파드 주입용) |

> **ConfigMap 패치만으로는 파드에 env가 주입되지 않습니다.**
> base Deployment가 `configMapKeyRef` 방식으로 env를 1:1 선언하므로,
> Deployment 패치도 함께 적용해야 합니다. 이 오버레이는 두 패치를 쌍으로 관리합니다.

---

## 왜 production과 production-ack1을 분리했는가

1. **휴먼에러 방지**: ACK=1 주입이 별도 오버레이에 격리되어 있어,
   실수로 `kubectl apply -k k8s/overlays/production`을 실행해도 ACK가 주입되지 않습니다.
   `production-ack1`은 명시적 의도 없이는 실행할 수 없습니다.

2. **감사 추적(Audit Trail)**: `production-ack1` 적용 이력이 git log와
   `kubectl` audit log에 별도로 기록되어, "누가 언제 ACK를 승인했는지"를 명확히 추적할 수 있습니다.

---

## 참고

- Backup 체크리스트: `ops/runbooks/db_backup_restore.md`
- Production 기본 오버레이: `k8s/overlays/production/README.md`
