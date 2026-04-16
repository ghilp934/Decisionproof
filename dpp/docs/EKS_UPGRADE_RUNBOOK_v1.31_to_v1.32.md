# EKS Upgrade Runbook: v1.31 → v1.32
# Cluster: dpp-production / Region: ap-northeast-2
# Generated: 2026-02-18

## ⚠️ 중단 불가 원칙
> Kubernetes 버전 업그레이드는 중단/일시정지가 불가합니다.
> Preflight에서 FAIL이 1개라도 있으면 절대 실행 단계로 진입하지 마십시오.
> 다운그레이드 불가 — 문제 발생 시 "이전 버전 새 클러스터 + 워크로드 마이그레이션"만 가능.

---

## STEP 0 — 환경 변수 및 Evidence 디렉토리 준비

### (1) 목적
모든 출력 로그를 타임스탬프 기반 디렉토리에 저장. 증적 확보.

### (2) CLI
```bash
export AWS_REGION="ap-northeast-2"
export CLUSTER_NAME="dpp-production"
export TARGET_VERSION="1.32"
export NAMESPACE_APP="dpp-production"
TS="$(date +%Y%m%d_%H%M%S)"
export EVIDENCE_DIR="./evidence/eks_upgrade_${TS}"
mkdir -p "$EVIDENCE_DIR"
echo "EVIDENCE_DIR=$EVIDENCE_DIR" | tee "$EVIDENCE_DIR/00_env.txt"
```

### (3) 기대 결과
- `./evidence/eks_upgrade_YYYYMMDD_HHMMSS/` 디렉토리 생성 확인
- 이후 모든 커맨드는 이 EVIDENCE_DIR 기준으로 저장

---

## A) PREFLIGHT CHECKLIST — FAIL이면 STOP

---

### [P-1] AWS 계정/리전/권한 확인

**목적**: 올바른 AWS 계정으로 EKS 업그레이드 권한이 있는지 검증

**콘솔 경로**: AWS Console → 우측 상단 계정명/리전 확인

**CLI**:
```bash
aws sts get-caller-identity | tee "$EVIDENCE_DIR/00_sts_identity.json"
aws configure list | tee "$EVIDENCE_DIR/00_aws_configure_list.txt"
```

**기대 결과/판정**:
| 항목 | 기대값 | 판정 |
|------|--------|------|
| Account | 예상 AWS 계정 ID | ✅/❌ |
| Region | ap-northeast-2 | ✅/❌ |
| 권한 | eks:UpdateClusterVersion, eks:UpdateNodegroupVersion 포함 | ✅/❌ |

**실패 시 즉시 중단 조건**:
- 계정 ID가 예상과 다름 → **STOP**: 잘못된 계정/프로파일, `AWS_PROFILE` 환경변수 확인
- 권한 없음 → **STOP**: IAM 정책 보강 후 재시도

---

### [P-2] kubeconfig 연결 및 클러스터 상태 스냅샷

**목적**: 업그레이드 전 클러스터 기준선(Baseline) 확보. 이미 깨진 노드/파드가 있으면 업그레이드 후 원인 식별 불가.

**콘솔 경로**: Amazon EKS → Clusters → dpp-production → Overview

**CLI**:
```bash
aws eks update-kubeconfig \
  --region "$AWS_REGION" \
  --name "$CLUSTER_NAME" \
  | tee "$EVIDENCE_DIR/01_update_kubeconfig.txt"

kubectl cluster-info \
  | tee "$EVIDENCE_DIR/01_cluster_info.txt"

kubectl version \
  | tee "$EVIDENCE_DIR/01_kubectl_version.txt"

kubectl get nodes -o wide \
  | tee "$EVIDENCE_DIR/01_nodes.txt"

kubectl get ns \
  | tee "$EVIDENCE_DIR/01_namespaces.txt"

kubectl get pods -A -o wide \
  | tee "$EVIDENCE_DIR/01_pods_all.txt"

kubectl get pods -A --field-selector=status.phase!=Running -o wide \
  | tee "$EVIDENCE_DIR/01_pods_not_running.txt"
```

**기대 결과/판정**:
| 항목 | 기대값 | 판정 |
|------|--------|------|
| 노드 상태 | 전부 Ready | ✅/❌ |
| 노드 K8s 버전 | v1.31.x | ✅/❌ |
| Not-Running 파드 | 0개 (또는 정상 범주의 Completed/Evicted) | ✅/❌ |
| 핵심 시스템 파드 (kube-system) | Running | ✅/❌ |

**실패 시 즉시 중단 조건**:
- 노드 NotReady → **STOP**: 원인 `kubectl describe node <node>` 확인 후 복구
- CrashLoopBackOff/Error 파드 다수 존재 → **STOP**: 선행 장애 해결 우선
- kubectl 연결 실패 → **STOP**: kubeconfig/네트워크 확인

---

### [P-3] EKS 클러스터/노드그룹/애드온 현황 수집

**목적**: 현재 클러스터 구성(버전, 노드그룹, 애드온)의 전체 스냅샷

**콘솔 경로**:
- Amazon EKS → Clusters → dpp-production → Configuration / Compute / Add-ons 탭

**CLI**:
```bash
# 클러스터 상세
aws eks describe-cluster \
  --region "$AWS_REGION" \
  --name "$CLUSTER_NAME" \
  | tee "$EVIDENCE_DIR/02_describe_cluster.json"

# 노드그룹 목록
aws eks list-nodegroups \
  --region "$AWS_REGION" \
  --cluster-name "$CLUSTER_NAME" \
  | tee "$EVIDENCE_DIR/02_list_nodegroups.json"

# 애드온 목록
aws eks list-addons \
  --region "$AWS_REGION" \
  --cluster-name "$CLUSTER_NAME" \
  | tee "$EVIDENCE_DIR/02_list_addons.json"

# 각 애드온 상세
for a in vpc-cni coredns kube-proxy aws-ebs-csi-driver; do
  aws eks describe-addon \
    --region "$AWS_REGION" \
    --cluster-name "$CLUSTER_NAME" \
    --addon-name "$a" \
    2>/dev/null \
    | tee "$EVIDENCE_DIR/02_describe_addon_${a}.json" \
    || echo "addon $a not found"
done
```

**기대 결과/판정**:
| 항목 | 기대값 | 판정 |
|------|--------|------|
| cluster.version | 1.31 | ✅/❌ |
| cluster.status | ACTIVE | ✅/❌ |
| 각 애드온 status | ACTIVE | ✅/❌ |

**실패 시 즉시 중단 조건**:
- cluster.status ≠ ACTIVE → **STOP**: 클러스터 이상, AWS Support 연락
- 애드온 status = DEGRADED/CREATE_FAILED → **STOP**: 애드온 복구 우선

---

### [P-4] Upgrade Insights 확인 ★ 가장 중요

**목적**: AWS가 자동 분석한 v1.32 업그레이드 차단 요인(ERROR) 및 경고(WARNING) 확인.
**이 단계 ERROR = 절대 진행 불가.**

**콘솔 경로** (권장):
```
AWS Console
→ Amazon EKS
→ Clusters
→ [dpp-production]
→ 상단 탭: "Insights" 또는 "Upgrade insights"
→ Kubernetes version: 1.32 필터 적용
→ ERROR 항목 전체 스크린샷 저장
```

**CLI**:
```bash
# Insights 목록 (UPGRADE_READINESS 필터)
aws eks list-insights \
  --region "$AWS_REGION" \
  --cluster-name "$CLUSTER_NAME" \
  --filter '{"category": "UPGRADE_READINESS"}' \
  | tee "$EVIDENCE_DIR/03_list_insights.json"

# 각 insight 상세 (list 결과의 id 값으로 대체)
# INSIGHT_IDS 는 위 list-insights 결과에서 .insights[].id 추출
INSIGHT_IDS=$(aws eks list-insights \
  --region "$AWS_REGION" \
  --cluster-name "$CLUSTER_NAME" \
  --filter '{"category": "UPGRADE_READINESS"}' \
  --query 'insights[].id' \
  --output text 2>/dev/null)

for id in $INSIGHT_IDS; do
  aws eks describe-insight \
    --region "$AWS_REGION" \
    --cluster-name "$CLUSTER_NAME" \
    --id "$id" \
    | tee "$EVIDENCE_DIR/03_describe_insight_${id}.json"
done
```

**기대 결과/판정**:
| severity | 판정 기준 | 조치 |
|----------|-----------|------|
| ERROR | 1개라도 있으면 → **STOP** | 해결 후 재확인 |
| WARNING | 원인·영향·대응 명시 후 사용자 승인 시 진행 | 명시적 승인 필요 |
| PASSING | 모두 PASSING → GO ✅ | 진행 가능 |

**알려진 v1.32 주요 ERROR 사례**:
- Deprecated API 사용: `kubernetes.io/ingress.class` 어노테이션 (현재 `ingress.yaml:10` 에 존재 — 업그레이드 전 수정 권장)
- `flowcontrol.apiserver.k8s.io/v1beta3` 사용 중인 리소스
- 삭제된 API 버전 사용 (CRD 포함)

**실패 시 즉시 중단 조건**:
- ERROR severity insight 존재 → **STOP, 무조건**
- `kubernetes.io/ingress.class` 사용 ERROR 발생 시 → `ingress.yaml` 수정 후 재확인

---

### [P-5] Upgrade Policy 확인

**목적**: 클러스터가 Standard Support vs Extended Support 여부 확인.
Extended Support 구간이면 비용($0.60/cluster-hr) 발생 중.

**콘솔 경로**:
```
Amazon EKS → Clusters 목록 → "Upgrade policy" 컬럼 확인
```

**CLI**:
```bash
aws eks describe-cluster \
  --region "$AWS_REGION" \
  --name "$CLUSTER_NAME" \
  --query "cluster.upgradePolicy" \
  --output json \
  | tee "$EVIDENCE_DIR/04_upgrade_policy.json"
```

**기대 결과/판정**:
| 결과 | 의미 | 권고 |
|------|------|------|
| `EXTENDED` | Extended Support 비용 발생 중 | 1.32 업그레이드 후 STANDARD 전환 검토 |
| `STANDARD` | 정상 Standard Support | 유지 |

**실패 시 즉시 중단 조건**: 없음 (정보 수집 단계)

---

### [P-6] 기존 K8s YAML 사전 검증 (ingress.yaml deprecated annotation)

**목적**: 현재 배포된 매니페스트 중 v1.32 Insights ERROR를 유발할 수 있는 항목 선제 처리.

**현재 저장소에서 발견된 이슈**:
```
파일: k8s/ingress.yaml:10
이슈: kubernetes.io/ingress.class: alb  ← deprecated since k8s v1.18
영향: v1.32 Upgrade Insights ERROR 유발 가능
```

**사전 수정 권장** (업그레이드 실행 전):
```yaml
# k8s/ingress.yaml 수정 내용
# annotations 에서 아래 줄 제거:
#   kubernetes.io/ingress.class: alb

# spec 섹션에 아래 추가:
spec:
  ingressClassName: alb   # ← 신규 필드 방식
  rules:
    ...
```

**CLI 검증 (수정 후)**:
```bash
kubectl get ingress -n dpp-production -o yaml | grep -E "ingress.class|ingressClassName" \
  | tee "$EVIDENCE_DIR/05_ingress_annotation_check.txt"
```

**판정**: 클러스터에 `kubernetes.io/ingress.class` 어노테이션이 살아있으면 Insights가 WARNING→ERROR로 전환될 수 있음.

---

### [P 최종 판정표]

아래 항목을 모두 채운 후 **전부 ✅ 여야 EXECUTION 진입 가능**:

```
[ ] P-1: AWS 계정/리전/권한 ✅
[ ] P-2: 클러스터 전 파드 Running ✅
[ ] P-2: 전 노드 Ready + v1.31.x ✅
[ ] P-3: cluster.status = ACTIVE ✅
[ ] P-3: 전 애드온 status = ACTIVE ✅
[ ] P-4: Insights ERROR = 0 ✅
[ ] P-4: WARNING → 내용 확인 후 명시적 승인 ✅
[ ] P-5: Upgrade Policy 확인 완료 ✅
[ ] P-6: deprecated annotation 처리 완료 또는 Insights 미검출 확인 ✅
```

---

## B) EXECUTION CHECKLIST — Preflight 전부 ✅ 후에만 진입

---

### [E-1] Control Plane 업그레이드 (1.31 → 1.32)

**목적**: EKS 컨트롤플레인(API server, etcd, controller-manager 등)을 1.32로 올림.
소요시간: 약 10~20분. 이 단계 시작 후 중단 불가.

**콘솔 경로** (권장):
```
Amazon EKS → Clusters → [dpp-production]
→ Overview → "Kubernetes version" 옆 [Upgrade] 버튼
→ Target version: 1.32 선택
→ [Upgrade] 확인
→ 진행 상태 스크린샷 저장 ($EVIDENCE_DIR/10_cp_upgrade_screenshot.png)
```

**CLI 대안**:
```bash
aws eks update-cluster-version \
  --region "$AWS_REGION" \
  --name "$CLUSTER_NAME" \
  --kubernetes-version "$TARGET_VERSION" \
  | tee "$EVIDENCE_DIR/10_update_cluster_version.json"

# update-id 추출
UPDATE_ID=$(cat "$EVIDENCE_DIR/10_update_cluster_version.json" | python3 -c "import sys,json; print(json.load(sys.stdin)['update']['id'])")
echo "UPDATE_ID=$UPDATE_ID" | tee "$EVIDENCE_DIR/10_update_id.txt"

# 상태 모니터링 (완료될 때까지 반복)
watch -n 30 "aws eks describe-update \
  --region $AWS_REGION \
  --name $CLUSTER_NAME \
  --update-id $UPDATE_ID \
  | tee $EVIDENCE_DIR/10_describe_update.json \
  | python3 -c \"import sys,json; u=json.load(sys.stdin)['update']; print(u['status'],u.get('errors',[]))\""
```

**기대 결과/판정**:
| 항목 | 기대값 | 판정 |
|------|--------|------|
| update.status | Successful | ✅ → 다음 단계 |
| update.status | InProgress | ⏳ → 대기 |
| update.status | Failed/Cancelled | ❌ → STOP |

**실패 시 즉시 중단 조건**:
- status = Failed → **STOP**: describe-update.errors 내용 확인, AWS Support 케이스 오픈
- 컨트롤플레인은 EKS가 이전 버전으로 자동 복구 시도할 수 있음 (보장 없음)

---

### [E-2] EKS Add-ons 업데이트

**목적**: 컨트롤플레인 1.32 확정 후, 각 Add-on을 1.32 호환 버전으로 업데이트.
순서: vpc-cni → coredns → kube-proxy → (있으면) aws-ebs-csi-driver

**콘솔 경로**:
```
Amazon EKS → Clusters → [dpp-production] → Add-ons 탭
→ 각 Add-on 카드 → [Update now] → 권장/호환 버전 선택 → [Update]
```

**CLI**:
```bash
# 컨트롤플레인 업그레이드 후 현황 재확인
aws eks list-addons \
  --region "$AWS_REGION" \
  --cluster-name "$CLUSTER_NAME" \
  | tee "$EVIDENCE_DIR/20_list_addons_after_cp.json"

# vpc-cni (가장 먼저)
aws eks update-addon \
  --region "$AWS_REGION" \
  --cluster-name "$CLUSTER_NAME" \
  --addon-name vpc-cni \
  --resolve-conflicts PRESERVE \
  | tee "$EVIDENCE_DIR/20_update_addon_vpc_cni.json"

sleep 30  # 업데이트 시작 대기

# coredns
aws eks update-addon \
  --region "$AWS_REGION" \
  --cluster-name "$CLUSTER_NAME" \
  --addon-name coredns \
  --resolve-conflicts PRESERVE \
  | tee "$EVIDENCE_DIR/20_update_addon_coredns.json"

# kube-proxy
aws eks update-addon \
  --region "$AWS_REGION" \
  --cluster-name "$CLUSTER_NAME" \
  --addon-name kube-proxy \
  --resolve-conflicts PRESERVE \
  | tee "$EVIDENCE_DIR/20_update_addon_kube_proxy.json"

# aws-ebs-csi-driver (설치된 경우만)
aws eks update-addon \
  --region "$AWS_REGION" \
  --cluster-name "$CLUSTER_NAME" \
  --addon-name aws-ebs-csi-driver \
  --resolve-conflicts PRESERVE \
  2>/dev/null | tee "$EVIDENCE_DIR/20_update_addon_ebs_csi.json" || echo "ebs-csi not installed, skipping"

# 완료 확인
for a in vpc-cni coredns kube-proxy; do
  aws eks describe-addon \
    --region "$AWS_REGION" \
    --cluster-name "$CLUSTER_NAME" \
    --addon-name "$a" \
    --query "addon.status" \
    --output text | xargs -I{} echo "$a: {}"
done | tee "$EVIDENCE_DIR/20_addon_status_check.txt"
```

**기대 결과/판정**:
| Add-on | 기대 status | 판정 |
|--------|-------------|------|
| vpc-cni | ACTIVE | ✅/❌ |
| coredns | ACTIVE | ✅/❌ |
| kube-proxy | ACTIVE | ✅/❌ |

**실패 시 즉시 중단 조건**:
- 어떤 Add-on이 DEGRADED → **STOP**: `describe-addon`으로 원인 확인, `--resolve-conflicts OVERWRITE`로 재시도 검토
- vpc-cni 업데이트 실패는 네트워크 장애로 이어질 수 있으므로 특히 주의

---

### [E-3] Managed Node Group 업그레이드

**목적**: 워커노드의 K8s 버전 및 AMI를 1.32로 교체 (Rolling 방식, 노드 교체).
소요시간: 노드 수 × 약 5~10분. 앱 파드는 자동 재배치됨.

**콘솔 경로**:
```
Amazon EKS → Clusters → [dpp-production]
→ Compute 탭 → Node groups
→ 각 노드그룹 클릭 → [Update version]
→ Kubernetes version: 1.32, Update strategy: Rolling update 선택
→ [Update]
```

**CLI**:
```bash
# 노드그룹 목록 확인
NODEGROUPS=$(aws eks list-nodegroups \
  --region "$AWS_REGION" \
  --cluster-name "$CLUSTER_NAME" \
  --query "nodegroups[]" \
  --output text)
echo "NodeGroups: $NODEGROUPS" | tee "$EVIDENCE_DIR/30_nodegroups.txt"

# 각 노드그룹 순차 업그레이드
for ng in $NODEGROUPS; do
  echo "=== Upgrading nodegroup: $ng ==="
  aws eks update-nodegroup-version \
    --region "$AWS_REGION" \
    --cluster-name "$CLUSTER_NAME" \
    --nodegroup-name "$ng" \
    --no-force \
    | tee "$EVIDENCE_DIR/30_update_nodegroup_${ng}.json"

  # update-id 추출 및 완료 대기
  NG_UPDATE_ID=$(cat "$EVIDENCE_DIR/30_update_nodegroup_${ng}.json" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['update']['id'])")

  echo "NG_UPDATE_ID for $ng: $NG_UPDATE_ID"

  # 완료될 때까지 대기 (최대 60분)
  for i in $(seq 1 72); do
    STATUS=$(aws eks describe-update \
      --region "$AWS_REGION" \
      --name "$CLUSTER_NAME" \
      --update-id "$NG_UPDATE_ID" \
      --query "update.status" --output text)
    echo "[$(date +%H:%M:%S)] $ng status: $STATUS"
    if [ "$STATUS" = "Successful" ]; then break; fi
    if [ "$STATUS" = "Failed" ] || [ "$STATUS" = "Cancelled" ]; then
      echo "FAIL: $ng upgrade failed"
      exit 1
    fi
    sleep 50
  done

  # 노드 상태 확인
  kubectl get nodes -o wide | tee "$EVIDENCE_DIR/30_nodes_after_${ng}.txt"
done
```

**기대 결과/판정**:
| 항목 | 기대값 | 판정 |
|------|--------|------|
| 노드 K8s 버전 | v1.32.x | ✅/❌ |
| 노드 상태 | 전부 Ready | ✅/❌ |
| 앱 파드 상태 | Running (재배치 완료) | ✅/❌ |

**실패 시 즉시 중단 조건**:
- 노드 NotReady 다수 발생 → **STOP**: 네트워크/AMI 이슈, AWS Support 연락
- CrashLoopBackOff 파드 급증 → **STOP**: 앱 호환성 이슈, 롤백 불가이므로 앱 수준 복구
- `--no-force` 옵션: PodDisruptionBudget(PDB)을 존중하며 교체. 만약 PDB로 인해 업그레이드가 blocking되면 원인 확인 후 `--force` 사용 여부를 결정해야 함

---

### [E-4] (선택) Upgrade Policy를 STANDARD로 전환

**목적**: Extended Support 비용($0.60/cluster-hr) 절감. 1.32는 Standard Support 버전.

**조건**: 현재 `upgradePolicy.supportType = EXTENDED` 일 경우에만 실행

**콘솔 경로**:
```
Amazon EKS → Clusters 목록 → dpp-production
→ Upgrade policy 컬럼 → Edit → Standard Support 선택
```

**CLI**:
```bash
aws eks update-cluster-config \
  --region "$AWS_REGION" \
  --name "$CLUSTER_NAME" \
  --upgrade-policy supportType=STANDARD \
  | tee "$EVIDENCE_DIR/40_set_upgrade_policy_standard.json"

# 확인
aws eks describe-cluster \
  --region "$AWS_REGION" \
  --name "$CLUSTER_NAME" \
  --query "cluster.upgradePolicy" \
  --output json | tee "$EVIDENCE_DIR/40_upgrade_policy_after.json"
```

---

## C) POSTFLIGHT CHECKLIST

---

### [F-1] 버전 정합성 최종 확인

**CLI**:
```bash
aws eks describe-cluster \
  --region "$AWS_REGION" \
  --name "$CLUSTER_NAME" \
  --query "cluster.version" \
  --output text \
  | tee "$EVIDENCE_DIR/50_cluster_version.txt"

kubectl get nodes -o wide \
  | tee "$EVIDENCE_DIR/50_nodes_after.txt"

kubectl get pods -A -o wide \
  | tee "$EVIDENCE_DIR/50_pods_all_after.txt"

kubectl get pods -A --field-selector=status.phase!=Running -o wide \
  | tee "$EVIDENCE_DIR/50_pods_not_running_after.txt"
```

**판정 기준**:
| 항목 | 기대값 |
|------|--------|
| cluster.version | 1.32 |
| 모든 노드 버전 | v1.32.x |
| 모든 노드 상태 | Ready |
| Not-Running 파드 | 0 (또는 업그레이드 전과 동일 수준) |

---

### [F-2] 애드온 상태 최종 확인

**CLI**:
```bash
for a in vpc-cni coredns kube-proxy aws-ebs-csi-driver; do
  aws eks describe-addon \
    --region "$AWS_REGION" \
    --cluster-name "$CLUSTER_NAME" \
    --addon-name "$a" \
    2>/dev/null | tee "$EVIDENCE_DIR/51_describe_addon_after_${a}.json" || true
done
```

---

### [F-3] 앱 헬스체크 (dpp-production 네임스페이스)

**CLI**:
```bash
kubectl -n "$NAMESPACE_APP" get deploy,sts,svc,ing -o wide \
  | tee "$EVIDENCE_DIR/60_app_resources.txt"

kubectl -n "$NAMESPACE_APP" get pods -o wide \
  | tee "$EVIDENCE_DIR/60_app_pods.txt"

# 각 Deployment 롤아웃 확인
for deploy in dpp-api dpp-worker dpp-reaper; do
  kubectl -n "$NAMESPACE_APP" rollout status deploy/$deploy --timeout=5m \
    | tee "$EVIDENCE_DIR/60_rollout_${deploy}.txt"
done

# API 헬스체크 (ALB endpoint 있으면)
# API_ENDPOINT=$(kubectl get svc dpp-api -n dpp-production -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
# curl -s http://${API_ENDPOINT}/health | tee "$EVIDENCE_DIR/60_health_check.json"
# curl -s http://${API_ENDPOINT}/readyz | tee "$EVIDENCE_DIR/60_readyz_check.json"
```

---

### [F-4] Upgrade Insights 재확인

**CLI**:
```bash
aws eks list-insights \
  --region "$AWS_REGION" \
  --cluster-name "$CLUSTER_NAME" \
  --filter '{"category": "UPGRADE_READINESS"}' \
  | tee "$EVIDENCE_DIR/70_list_insights_after.json"
```

**판정**: 잔존 ERROR 없어야 함. WARNING은 내용 검토.

---

### [F 최종 판정표]

```
[ ] F-1: cluster.version = 1.32 ✅
[ ] F-1: 모든 노드 v1.32.x Ready ✅
[ ] F-1: Not-Running 파드 = 0 ✅
[ ] F-2: 모든 Add-on ACTIVE ✅
[ ] F-3: dpp-api/worker/reaper Deployment 정상 ✅
[ ] F-3: /health /readyz 200 응답 ✅
[ ] F-4: Insights ERROR = 0 ✅
```

---

## D) ABORT / ROLLBACK 조건 및 대안

### 중단 불가 원칙
- 업그레이드는 일시정지/중단 불가
- **Kubernetes 버전 다운그레이드는 절대 불가**

### 단계별 실패 대안

| 단계 | 실패 상황 | 대안 |
|------|-----------|------|
| P-4 Insights ERROR | 업그레이드 차단 | ERROR 원인 해결 후 재시도 |
| E-1 CP 업그레이드 실패 | 클러스터 이전 버전 유지 가능성 | describe-update 수집 → AWS Support |
| E-2 Add-on 업데이트 실패 | 파드 네트워크/DNS 이상 가능 | `--resolve-conflicts OVERWRITE`로 재시도 또는 수동 재설치 |
| E-3 노드그룹 업그레이드 실패 | 노드 혼재 상태 (v1.31 + v1.32) | 실패한 노드그룹만 재시도 또는 Manual 교체 |
| 앱 장애 (업그레이드 후) | 앱 코드 호환성 문제 | **롤백 불가**: 앱 수준 버그픽스 배포 필요 |
| 전면 장애 | 클러스터 복구 불가 | 이전 버전(1.31) 신규 클러스터 생성 + 워크로드 마이그레이션 |

### 즉시 중단 트리거 (STOP 조건 요약)

```
Preflight:
  ❌ AWS 계정/권한 불일치
  ❌ 노드 NotReady 또는 파드 다수 장애
  ❌ cluster.status ≠ ACTIVE
  ❌ Insights ERROR 1개 이상
  ❌ Add-on status DEGRADED

Execution:
  ❌ update-cluster-version status = Failed
  ❌ Add-on 업데이트 DEGRADED 지속
  ❌ 노드그룹 교체 중 노드 다수 NotReady
  ❌ CrashLoop 파드 급증
```

---

## E) 실행 결과 붙여넣기 포맷 (Preflight 판정용)

Preflight 실행 후 아래 4개 파일 내용을 붙여넣어 주세요. 민감정보(계정ID, 엔드포인트, 비밀번호)는 `<MASKED>` 처리.

```
--- [ 1. 02_describe_cluster.json ] ---
(여기에 붙여넣기)

--- [ 2. 03_list_insights.json ] ---
(여기에 붙여넣기)

--- [ 3. 03_describe_insight_<ID>.json (ERROR/WARNING 있는 것만) ] ---
(여기에 붙여넣기)

--- [ 4. 02_list_nodegroups.json ] ---
(여기에 붙여넣기)

--- [ 5. 02_list_addons.json ] ---
(여기에 붙여넣기)

--- [ 6. 01_pods_not_running.txt (Not-Running 파드 목록) ] ---
(여기에 붙여넣기)
```

---

## 참조: 현재 저장소 K8s 매니페스트 현황

### 수정 필요 항목 (업그레이드 전 처리 권장)

| 파일 | 라인 | 현재값 | 수정값 | 우선순위 |
|------|------|--------|--------|---------|
| `k8s/ingress.yaml:10` | annotations | `kubernetes.io/ingress.class: alb` | `spec.ingressClassName: alb` 으로 이동 | 🔴 HIGH |
| `k8s/api-deployment.yaml` | containers | securityContext 없음 | securityContext 추가 | 🟡 MEDIUM |
| `k8s/worker-deployment.yaml` | containers | securityContext 없음 | securityContext 추가 | 🟡 MEDIUM |
| `k8s/reaper-deployment.yaml` | containers | securityContext 없음 | securityContext 추가 | 🟡 MEDIUM |

### 이미 최신 상태 (변경 불필요)

| 파일 | apiVersion | 상태 |
|------|------------|------|
| `api-deployment.yaml` | `apps/v1` | ✅ v1.32 유효 |
| `worker-deployment.yaml` | `apps/v1`, `autoscaling/v2` | ✅ v1.32 유효 |
| `reaper-deployment.yaml` | `apps/v1` | ✅ v1.32 유효 |
| `ingress.yaml` | `networking.k8s.io/v1` | ✅ v1.32 유효 (annotation만 수정) |

---

**런북 버전**: 1.0
**대상 클러스터**: dpp-production (ap-northeast-2)
**업그레이드 경로**: v1.31 → v1.32
**작성일**: 2026-02-18
**작성자**: SRE Copilot (Claude Code)
