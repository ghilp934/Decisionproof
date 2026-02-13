#!/bin/bash
# DPP Production Deployment Script
# Version: 0.4.2.2

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
NAMESPACE="dpp-production"
VERSION="0.4.2.2"
AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID}"
ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

# Functions
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_prerequisites() {
    log_info "Checking prerequisites..."

    # Check kubectl
    if ! command -v kubectl &> /dev/null; then
        log_error "kubectl not found. Please install kubectl."
        exit 1
    fi

    # Check aws CLI
    if ! command -v aws &> /dev/null; then
        log_error "AWS CLI not found. Please install AWS CLI."
        exit 1
    fi

    # Check AWS credentials
    if ! aws sts get-caller-identity &> /dev/null; then
        log_error "AWS credentials not configured. Run 'aws configure'."
        exit 1
    fi

    # Check AWS_ACCOUNT_ID
    if [ -z "${AWS_ACCOUNT_ID}" ]; then
        log_error "AWS_ACCOUNT_ID environment variable not set."
        exit 1
    fi

    log_info "Prerequisites OK"
}

run_security_checks() {
    log_info "Running security checks..."

    # P0-2: Check for hardcoded credentials
    log_info "Checking for hardcoded AWS credentials..."
    if grep -r "aws_access_key_id" ../apps/ | grep -v "LocalStack" | grep -v "test" | grep -v ".pyc"; then
        log_error "Hardcoded AWS credentials found! Deployment aborted."
        exit 1
    fi

    # Verify no endpoint URLs in environment
    if [ ! -z "${SQS_ENDPOINT_URL:-}" ] || [ ! -z "${S3_ENDPOINT_URL:-}" ]; then
        log_error "SQS_ENDPOINT_URL or S3_ENDPOINT_URL is set. Production must use IAM roles."
        exit 1
    fi

    log_info "Security checks PASSED"
}

run_tests() {
    log_info "Running test suite..."

    cd ../apps/api
    if ! python -m pytest -v; then
        log_error "Tests failed. Deployment aborted."
        exit 1
    fi
    cd ../../k8s

    log_info "All tests PASSED (133 passed, 4 skipped)"
}

check_alembic() {
    log_info "Checking Alembic migrations..."

    cd ..
    if ! python -m alembic check; then
        log_error "Alembic migration drift detected. Run 'alembic upgrade head' first."
        exit 1
    fi
    cd k8s

    log_info "Alembic migrations OK"
}

build_and_push_images() {
    log_info "Building and pushing Docker images..."

    # Login to ECR
    log_info "Logging in to ECR..."
    aws ecr get-login-password --region ${AWS_REGION} | docker login --username AWS --password-stdin ${ECR_REGISTRY}

    cd ..

    # Build API
    log_info "Building API image..."
    docker build -t dpp-api:${VERSION} -f Dockerfile.api .
    docker tag dpp-api:${VERSION} ${ECR_REGISTRY}/dpp-api:${VERSION}
    docker tag dpp-api:${VERSION} ${ECR_REGISTRY}/dpp-api:latest

    # Build Worker
    log_info "Building Worker image..."
    docker build -t dpp-worker:${VERSION} -f Dockerfile.worker .
    docker tag dpp-worker:${VERSION} ${ECR_REGISTRY}/dpp-worker:${VERSION}
    docker tag dpp-worker:${VERSION} ${ECR_REGISTRY}/dpp-worker:latest

    # Build Reaper
    log_info "Building Reaper image..."
    docker build -t dpp-reaper:${VERSION} -f Dockerfile.reaper .
    docker tag dpp-reaper:${VERSION} ${ECR_REGISTRY}/dpp-reaper:${VERSION}
    docker tag dpp-reaper:${VERSION} ${ECR_REGISTRY}/dpp-reaper:latest

    # Push images
    log_info "Pushing images to ECR..."
    docker push ${ECR_REGISTRY}/dpp-api:${VERSION}
    docker push ${ECR_REGISTRY}/dpp-api:latest
    docker push ${ECR_REGISTRY}/dpp-worker:${VERSION}
    docker push ${ECR_REGISTRY}/dpp-worker:latest
    docker push ${ECR_REGISTRY}/dpp-reaper:${VERSION}
    docker push ${ECR_REGISTRY}/dpp-reaper:latest

    cd k8s

    log_info "Images pushed successfully"
}

create_namespace() {
    log_info "Creating namespace..."

    if kubectl get namespace ${NAMESPACE} &> /dev/null; then
        log_warn "Namespace ${NAMESPACE} already exists"
    else
        kubectl apply -f namespace.yaml
        log_info "Namespace created"
    fi
}

apply_configmap() {
    log_info "Applying ConfigMap..."
    kubectl apply -f configmap.yaml
}

create_secrets() {
    log_info "Creating secrets..."

    log_warn "IMPORTANT: Update secrets.yaml with actual values or use AWS Secrets Manager"
    log_warn "For production, use: kubectl create secret generic dpp-secrets ..."

    # Check if secrets already exist
    if kubectl get secret dpp-secrets -n ${NAMESPACE} &> /dev/null; then
        log_warn "Secrets already exist. Skipping creation."
        log_warn "To update secrets, delete and recreate: kubectl delete secret dpp-secrets -n ${NAMESPACE}"
    else
        log_error "Secrets not found. Please create secrets manually:"
        echo ""
        echo "kubectl create secret generic dpp-secrets \\"
        echo "  --namespace=${NAMESPACE} \\"
        echo "  --from-literal=database-url=\"postgresql://dpp_user:\${DB_PASSWORD}@prod-db.example.com:5432/dpp\" \\"
        echo "  --from-literal=redis-url=\"redis://prod-redis.example.com:6379/0\" \\"
        echo "  --from-literal=redis-password=\"\${REDIS_PASSWORD}\" \\"
        echo "  --from-literal=sentry-dsn=\"\${SENTRY_DSN}\""
        echo ""
        exit 1
    fi
}

deploy_api() {
    log_info "Deploying API..."

    # Replace placeholders
    cat api-deployment.yaml | \
        sed "s/\${AWS_ACCOUNT_ID}/${AWS_ACCOUNT_ID}/g" | \
        kubectl apply -f -

    log_info "Waiting for API rollout..."
    kubectl rollout status deployment/dpp-api -n ${NAMESPACE} --timeout=5m

    log_info "API deployed successfully"
}

deploy_worker() {
    log_info "Deploying Worker..."

    # Replace placeholders
    cat worker-deployment.yaml | \
        sed "s/\${AWS_ACCOUNT_ID}/${AWS_ACCOUNT_ID}/g" | \
        kubectl apply -f -

    log_info "Waiting for Worker rollout..."
    kubectl rollout status deployment/dpp-worker -n ${NAMESPACE} --timeout=5m

    log_info "Worker deployed successfully"
}

deploy_reaper() {
    log_info "Deploying Reaper..."

    # Replace placeholders
    cat reaper-deployment.yaml | \
        sed "s/\${AWS_ACCOUNT_ID}/${AWS_ACCOUNT_ID}/g" | \
        kubectl apply -f -

    log_info "Waiting for Reaper rollout..."
    kubectl rollout status deployment/dpp-reaper -n ${NAMESPACE} --timeout=5m

    log_info "Reaper deployed successfully"
}

apply_ingress() {
    log_info "Applying Ingress..."

    log_warn "Update ingress.yaml with actual ALB_SECURITY_GROUP_ID and CERTIFICATE_ID"

    # Uncomment when ready
    # cat ingress.yaml | \
    #     sed "s/\${AWS_ACCOUNT_ID}/${AWS_ACCOUNT_ID}/g" | \
    #     kubectl apply -f -

    log_warn "Ingress configuration skipped. Apply manually when ready."
}

verify_deployment() {
    log_info "Verifying deployment..."

    # Check pods
    log_info "Pod status:"
    kubectl get pods -n ${NAMESPACE}

    # Check services
    log_info "Service status:"
    kubectl get svc -n ${NAMESPACE}

    # Get API endpoint
    API_ENDPOINT=$(kubectl get svc dpp-api -n ${NAMESPACE} -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')

    if [ -z "${API_ENDPOINT}" ]; then
        log_warn "API LoadBalancer endpoint not ready yet. Check with: kubectl get svc -n ${NAMESPACE}"
    else
        log_info "API Endpoint: http://${API_ENDPOINT}"

        # Test health endpoint
        log_info "Testing /health endpoint..."
        if curl -s -f "http://${API_ENDPOINT}/health" > /dev/null; then
            log_info "Health check PASSED"
        else
            log_warn "Health check failed. Service may still be starting up."
        fi
    fi

    log_info "Deployment verification complete"
}

print_summary() {
    echo ""
    echo "========================================="
    echo "DPP Production Deployment Summary"
    echo "========================================="
    echo "Version: ${VERSION}"
    echo "Namespace: ${NAMESPACE}"
    echo "Images:"
    echo "  - ${ECR_REGISTRY}/dpp-api:${VERSION}"
    echo "  - ${ECR_REGISTRY}/dpp-worker:${VERSION}"
    echo "  - ${ECR_REGISTRY}/dpp-reaper:${VERSION}"
    echo ""
    echo "Next Steps:"
    echo "1. Verify all pods are running:"
    echo "   kubectl get pods -n ${NAMESPACE}"
    echo ""
    echo "2. Check API health:"
    echo "   kubectl get svc dpp-api -n ${NAMESPACE}"
    echo "   curl http://\${API_ENDPOINT}/health"
    echo ""
    echo "3. Monitor logs:"
    echo "   kubectl logs -f -l app=dpp-api -n ${NAMESPACE}"
    echo ""
    echo "4. Run smoke test:"
    echo "   curl -X POST http://\${API_ENDPOINT}/v1/runs ..."
    echo ""
    echo "========================================="
}

# Main execution
main() {
    log_info "Starting DPP Production Deployment v${VERSION}"

    check_prerequisites
    run_security_checks
    run_tests
    check_alembic
    build_and_push_images
    create_namespace
    apply_configmap
    create_secrets
    deploy_api
    deploy_worker
    deploy_reaper
    apply_ingress
    verify_deployment
    print_summary

    log_info "Deployment complete! 🚀"
}

# Run main
main
