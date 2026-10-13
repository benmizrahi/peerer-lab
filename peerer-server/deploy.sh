#!/usr/bin/env bash
# ==============================================================================
# Peerer Cloud Run Broker Deployment Script
# ==============================================================================
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-peerer-broker}"
REGION="${REGION:-us-central1}"
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || echo "")}"
MIN_INSTANCES="${MIN_INSTANCES:-0}"
MAX_INSTANCES="${MAX_INSTANCES:-10}"
MEMORY="${MEMORY:-512Mi}"
CPU="${CPU:-1}"
AUTH_TOKEN="${BROKER_AUTH_TOKEN:-}"

echo "========================================================"
echo " Deploying Peerer Broker Server to Google Cloud Run"
echo "========================================================"

if [ -z "$PROJECT_ID" ]; then
    echo "ERROR: GCP project ID could not be determined."
    echo "Please set PROJECT_ID environment variable or run 'gcloud config set project <your-project>'."
    exit 1
fi

if [ -z "$AUTH_TOKEN" ]; then
    echo "Generating secure random BROKER_AUTH_TOKEN for this deployment..."
    AUTH_TOKEN=$(openssl rand -hex 32 2>/dev/null || python3 -c 'import secrets; print(secrets.token_hex(32))')
    echo "Generated Token: $AUTH_TOKEN"
    echo "IMPORTANT: Save this token! You will configure your peerer sidecars and clients with it."
fi

echo "Project:       $PROJECT_ID"
echo "Service Name:  $SERVICE_NAME"
echo "Region:        $REGION"
echo "Memory:        $MEMORY"
echo "CPU:           $CPU"
echo "Min Instances: $MIN_INSTANCES"
echo "========================================================"

# Deploy from source using Cloud Build & Cloud Run
gcloud run deploy "$SERVICE_NAME" \
    --source . \
    --project "$PROJECT_ID" \
    --region "$REGION" \
    --platform managed \
    --allow-unauthenticated \
    --min-instances "$MIN_INSTANCES" \
    --max-instances "$MAX_INSTANCES" \
    --memory "$MEMORY" \
    --cpu "$CPU" \
    --set-env-vars "BROKER_AUTH_TOKEN=$AUTH_TOKEN,STORAGE_BACKEND=memory"

# Fetch deployed URL
SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" --project "$PROJECT_ID" --region "$REGION" --format 'value(status.url)')

echo ""
echo "========================================================"
echo " Successfully Deployed Peerer Broker!"
echo " Service URL: $SERVICE_URL"
echo " Auth Token:  $AUTH_TOKEN"
echo "========================================================"
echo ""
echo "To connect an Antigravity instance to this broker, configure:"
echo "export PEER_BROKER_URL=\"$SERVICE_URL\""
echo "export PEER_BROKER_TOKEN=\"$AUTH_TOKEN\""
echo ""
