# DPP Kubernetes Deployment

Production-ready Kubernetes manifests for DPP API Platform v0.4.2.2

## 📁 Directory Structure

```
k8s/
├── namespace.yaml           # dpp-production namespace
├── configmap.yaml          # Environment variables
├── secrets.yaml            # Sensitive data (TEMPLATE)
├── api-deployment.yaml     # API deployment + service + IRSA
├── worker-deployment.yaml  # Worker deployment + HPA + IRSA
├── reaper-deployment.yaml  # Reaper deployment + IRSA
├── ingress.yaml            # ALB ingress + NetworkPolicy
├── deploy.sh               # Automated deployment script
└── README.md               # This file
```

## 🚀 Quick Start

### Prerequisites

1. **Kubernetes Cluster** (EKS recommended)
   ```bash
   eksctl create cluster \
     --name dpp-production \
     --region us-east-1 \
     --node-type m5.xlarge \
     --nodes 3 \
     --nodes-min 3 \
     --nodes-max 10 \
     --managed
   ```

2. **AWS Load Balancer Controller**
   ```bash
   helm repo add eks https://aws.github.io/eks-charts
   helm install aws-load-balancer-controller eks/aws-load-balancer-controller \
     -n kube-system \
     --set clusterName=dpp-production
   ```

3. **IAM Roles for Service Accounts (IRSA)**
   ```bash
   # Create OIDC provider
   eksctl utils associate-iam-oidc-provider \
     --cluster dpp-production \
     --approve

   # Create IAM roles (see PRODUCTION_DEPLOYMENT_GUIDE.md)
   aws iam create-role --role-name dpp-api-role --assume-role-policy-document file://api-trust-policy.json
   aws iam create-role --role-name dpp-worker-role --assume-role-policy-document file://worker-trust-policy.json
   aws iam create-role --role-name dpp-reaper-role --assume-role-policy-document file://reaper-trust-policy.json
   ```

4. **ECR Repositories**
   ```bash
   aws ecr create-repository --repository-name dpp-api
   aws ecr create-repository --repository-name dpp-worker
   aws ecr create-repository --repository-name dpp-reaper
   ```

### Deployment Steps

#### Option 1: Automated Deployment (Recommended)

```bash
# Set environment variables
export AWS_ACCOUNT_ID="123456789012"
export AWS_REGION="us-east-1"

# Run deployment script
cd k8s
chmod +x deploy.sh
./deploy.sh
```

The script will:
1. ✅ Run security checks (P0-2: no hardcoded credentials)
2. ✅ Run full test suite (133 tests)
3. ✅ Check Alembic migrations
4. ✅ Build and push Docker images to ECR
5. ✅ Create namespace and apply manifests
6. ✅ Deploy API, Worker, Reaper
7. ✅ Verify health checks

#### Option 2: Manual Deployment

```bash
# 1. Create namespace
kubectl apply -f namespace.yaml

# 2. Create secrets (IMPORTANT: Update with actual values)
kubectl create secret generic dpp-secrets \
  --namespace=dpp-production \
  --from-literal=database-url="postgresql://dpp_user:${DB_PASSWORD}@prod-db.example.com:5432/dpp" \
  --from-literal=redis-url="redis://prod-redis.example.com:6379/0" \
  --from-literal=redis-password="${REDIS_PASSWORD}" \
  --from-literal=sentry-dsn="${SENTRY_DSN}"

# 3. Apply ConfigMap
kubectl apply -f configmap.yaml

# 4. Build and push images
docker build -t ${AWS_ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com/dpp-api:0.4.2.2 -f ../Dockerfile.api ..
docker push ${AWS_ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com/dpp-api:0.4.2.2

docker build -t ${AWS_ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com/dpp-worker:0.4.2.2 -f ../Dockerfile.worker ..
docker push ${AWS_ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com/dpp-worker:0.4.2.2

docker build -t ${AWS_ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com/dpp-reaper:0.4.2.2 -f ../Dockerfile.reaper ..
docker push ${AWS_ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com/dpp-reaper:0.4.2.2

# 5. Deploy applications (replace ${AWS_ACCOUNT_ID} in manifests)
cat api-deployment.yaml | sed "s/\${AWS_ACCOUNT_ID}/${AWS_ACCOUNT_ID}/g" | kubectl apply -f -
cat worker-deployment.yaml | sed "s/\${AWS_ACCOUNT_ID}/${AWS_ACCOUNT_ID}/g" | kubectl apply -f -
cat reaper-deployment.yaml | sed "s/\${AWS_ACCOUNT_ID}/${AWS_ACCOUNT_ID}/g" | kubectl apply -f -

# 6. Wait for rollout
kubectl rollout status deployment/dpp-api -n dpp-production
kubectl rollout status deployment/dpp-worker -n dpp-production
kubectl rollout status deployment/dpp-reaper -n dpp-production

# 7. Apply ingress (update ALB_SECURITY_GROUP_ID and CERTIFICATE_ID first)
kubectl apply -f ingress.yaml
```

## 🔍 Verification

### Check Pod Status
```bash
kubectl get pods -n dpp-production

# Expected output:
# NAME                          READY   STATUS    RESTARTS   AGE
# dpp-api-xxxxx                 1/1     Running   0          2m
# dpp-api-yyyyy                 1/1     Running   0          2m
# dpp-api-zzzzz                 1/1     Running   0          2m
# dpp-worker-xxxxx              1/1     Running   0          2m
# dpp-worker-yyyyy              1/1     Running   0          2m
# dpp-reaper-xxxxx              1/1     Running   0          2m
```

### Check Service Health
```bash
# Get API endpoint
API_ENDPOINT=$(kubectl get svc dpp-api -n dpp-production -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')

# Test health endpoint
curl http://${API_ENDPOINT}/health
# Expected: {"status": "healthy", "version": "0.4.2.2"}

# Test readiness endpoint (P1-J)
curl http://${API_ENDPOINT}/readyz
# Expected: {"status": "ready", "services": {"database": "up", "redis": "up", ...}}
```

### Monitor Logs
```bash
# API logs
kubectl logs -f -l app=dpp-api -n dpp-production

# Worker logs (should show heartbeat every 30s)
kubectl logs -f -l app=dpp-worker -n dpp-production | grep "Heartbeat"

# Reaper logs (should show scan every 30s/60s)
kubectl logs -f -l app=dpp-reaper -n dpp-production | grep "Reaper scan"
```

### Check Auto-Scaling
```bash
# Check HPA status
kubectl get hpa -n dpp-production

# Expected output:
# NAME               REFERENCE               TARGETS         MINPODS   MAXPODS   REPLICAS   AGE
# dpp-worker-hpa     Deployment/dpp-worker   50%/70%, 60%/75%   5         10        5          5m
```

## 🛠️ Operations

### Scaling

#### Manual Scaling
```bash
# Scale API
kubectl scale deployment/dpp-api --replicas=5 -n dpp-production

# Scale Worker (if HPA is disabled)
kubectl scale deployment/dpp-worker --replicas=8 -n dpp-production
```

#### Auto-Scaling Configuration
Worker auto-scaling is configured in `worker-deployment.yaml`:
- **Min replicas**: 5
- **Max replicas**: 10
- **CPU target**: 70%
- **Memory target**: 75%

### Rolling Updates

```bash
# Update API image
kubectl set image deployment/dpp-api \
  dpp-api=${AWS_ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com/dpp-api:0.4.3.0 \
  -n dpp-production

# Check rollout status
kubectl rollout status deployment/dpp-api -n dpp-production

# Rollback if needed
kubectl rollout undo deployment/dpp-api -n dpp-production
```

### Debugging

```bash
# Get pod details
kubectl describe pod <pod-name> -n dpp-production

# Execute into pod
kubectl exec -it <pod-name> -n dpp-production -- /bin/bash

# Check events
kubectl get events -n dpp-production --sort-by='.lastTimestamp'

# Check resource usage
kubectl top pods -n dpp-production
kubectl top nodes
```

## 🔒 Security

### P0-2: AWS Credentials Verification

**CRITICAL**: Verify NO hardcoded credentials before deployment

```bash
# 1. Check codebase
cd ..
grep -r "aws_access_key_id" apps/ | grep -v "LocalStack" | grep -v "test"
# Expected: No results

# 2. Verify environment variables NOT set
echo "SQS_ENDPOINT_URL=${SQS_ENDPOINT_URL}"
echo "S3_ENDPOINT_URL=${S3_ENDPOINT_URL}"
# Expected: Both empty

# 3. Verify IRSA configured
kubectl get sa dpp-api -n dpp-production -o yaml | grep eks.amazonaws.com/role-arn
# Expected: arn:aws:iam::${AWS_ACCOUNT_ID}:role/dpp-api-role
```

### Network Security

Network policies are configured in `ingress.yaml`:
- API: Allow ALB ingress, Prometheus scraping, egress to DB/Redis/AWS
- Worker: Allow egress only (no ingress)
- Reaper: Allow egress only (no ingress)

### Secrets Management

**Option 1: Kubernetes Secrets** (simple)
```bash
kubectl create secret generic dpp-secrets --from-literal=...
```

**Option 2: AWS Secrets Manager + External Secrets Operator** (recommended)
```bash
# Install external-secrets
helm install external-secrets external-secrets/external-secrets -n kube-system

# Configure (see secrets.yaml for example)
kubectl apply -f secrets.yaml
```

## 📊 Monitoring

### Prometheus Metrics

API, Worker, and Reaper expose metrics on port 9090:

```bash
# Port-forward to access metrics locally
kubectl port-forward svc/dpp-api 9090:9090 -n dpp-production

# Access metrics
curl http://localhost:9090/metrics
```

### Grafana Dashboards

Import dashboards from `../docs/grafana/`:
- Money Flow Dashboard
- System Health Dashboard
- Worker Metrics Dashboard
- Reaper Activity Dashboard

### Critical Alerts

Configure alerts in Prometheus (see PRODUCTION_DEPLOYMENT_GUIDE.md):
- `DPP_AuditRequired_Critical`: Money leak detection
- `DPP_API_Down`: API unavailable
- `DPP_Database_Connection_Failed`: DB connection failure
- `DPP_SQS_Queue_Backlog`: Queue depth > 1000

## 🔄 Backup & Disaster Recovery

### Database Backups

RDS automated backups (configured externally):
- Daily snapshots
- 7-day retention
- Multi-AZ replication

### Application State

Stateless architecture - no persistent volumes required.
All state stored in PostgreSQL and Redis (backed up separately).

### Disaster Recovery Plan

1. **Database failure**: RDS auto-failover to standby (Multi-AZ)
2. **Redis failure**: Redis replica promotion
3. **Pod failure**: Kubernetes auto-restart
4. **Node failure**: Pods reschedule to healthy nodes
5. **Region failure**: Deploy to secondary region (manual)

## 📚 Additional Resources

- [PRODUCTION_DEPLOYMENT_GUIDE.md](../PRODUCTION_DEPLOYMENT_GUIDE.md): Complete deployment guide
- [IMPLEMENTATION_REPORT.md](../IMPLEMENTATION_REPORT.md): Technical implementation details
- [README.md](../README.md): Project overview

## 🆘 Support

- **On-Call Engineering**: PagerDuty rotation
- **DevOps Team**: devops@example.com
- **Slack Channel**: #dpp-platform

---

**Version**: 0.4.2.2
**Last Updated**: 2026-02-13
**Production Ready**: ✅ 100%
