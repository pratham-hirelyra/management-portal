#!/usr/bin/env bash
# JA Management Portal — Cloud Run + Cloud SQL deployment script
# Project: hirelyra-gcp-dev | Region: asia-south1
set -euo pipefail

# ─── Config ───────────────────────────────────────────────────────────────────
PROJECT_ID="hirelyra-gcp-dev"
REGION="asia-south1"
REGISTRY="${REGION}-docker.pkg.dev/${PROJECT_ID}/ja-management-portal"

SQL_INSTANCE="ja-management-portal-db"
DB_NAME="ja_management_portal"
DB_USER="ja_management_portal"

BACKEND_SERVICE="ja-management-portal-backend"
FRONTEND_SERVICE="ja-management-portal-frontend"
FRONTEND_DOMAIN="portal.justaccountants.in"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ─── Colours ──────────────────────────────────────────────────────────────────
BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RESET='\033[0m'
step() { echo -e "\n${BOLD}▸ $*${RESET}"; }
ok()   { echo -e "${GREEN}  ✓ $*${RESET}"; }
warn() { echo -e "${YELLOW}  ⚠ $*${RESET}"; }

# ─── DB Password ──────────────────────────────────────────────────────────────
if [ -z "${DB_PASSWORD:-}" ]; then
  DB_PASSWORD=$(openssl rand -base64 24 | tr -dc 'a-zA-Z0-9' | head -c 24)
  echo -e "\n${BOLD}╔══════════════════════════════════════════════════════════╗"
  echo    "  Generated DB password — SAVE THIS NOW"
  echo    "  DB_PASSWORD=${DB_PASSWORD}"
  echo -e "╚══════════════════════════════════════════════════════════╝${RESET}\n"
fi

# ─── 1. Enable APIs ───────────────────────────────────────────────────────────
step "Enabling GCP APIs (may take a minute)..."
gcloud services enable \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  --project="$PROJECT_ID"
ok "APIs enabled"

# ─── 2. Artifact Registry ─────────────────────────────────────────────────────
step "Setting up Artifact Registry..."
gcloud artifacts repositories create ja-management-portal \
  --repository-format=docker \
  --location="$REGION" \
  --project="$PROJECT_ID" \
  --quiet 2>/dev/null && ok "Repository created" || ok "Repository already exists"

gcloud auth print-access-token | sudo docker login \
  -u oauth2accesstoken --password-stdin \
  "https://${REGION}-docker.pkg.dev"
ok "Docker auth configured"

# ─── 3. Cloud SQL instance ────────────────────────────────────────────────────
step "Creating Cloud SQL instance (PostgreSQL 16) — takes ~5 min..."
gcloud sql instances create "$SQL_INSTANCE" \
  --database-version=POSTGRES_16 \
  --edition=ENTERPRISE \
  --tier=db-f1-micro \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --storage-type=SSD \
  --storage-size=10GB \
  --quiet 2>/dev/null && ok "SQL instance created" || ok "SQL instance already exists"

step "Creating database and user..."
gcloud sql databases create "$DB_NAME" \
  --instance="$SQL_INSTANCE" \
  --project="$PROJECT_ID" \
  --quiet 2>/dev/null && ok "Database '$DB_NAME' created" || ok "Database already exists"

gcloud sql users create "$DB_USER" \
  --instance="$SQL_INSTANCE" \
  --password="$DB_PASSWORD" \
  --project="$PROJECT_ID" \
  --quiet 2>/dev/null \
  || gcloud sql users set-password "$DB_USER" \
       --instance="$SQL_INSTANCE" \
       --password="$DB_PASSWORD" \
       --project="$PROJECT_ID" \
       --quiet
ok "User '$DB_USER' ready"

# ─── 4. Grant Cloud Run SA the Cloud SQL client role ──────────────────────────
step "Granting Cloud SQL access to Cloud Run service account..."
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
CLOUD_RUN_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${CLOUD_RUN_SA}" \
  --role="roles/cloudsql.client" \
  --quiet >/dev/null
ok "IAM binding set"

# ─── 5. Store ja-mp-service-account-key.json in Secret Manager ─────────────────────
SA_KEY_FILE="$SCRIPT_DIR/backend/service-account-key.json"
if [ -f "$SA_KEY_FILE" ]; then
  step "Storing service-account-key.json in Secret Manager..."
  gcloud secrets create service-account-key \
    --project="$PROJECT_ID" \
    --replication-policy=automatic \
    --quiet 2>/dev/null || true
  gcloud secrets versions add service-account-key \
    --data-file="$SA_KEY_FILE" \
    --project="$PROJECT_ID"
  gcloud secrets add-iam-policy-binding service-account-key \
    --member="serviceAccount:${CLOUD_RUN_SA}" \
    --role="roles/secretmanager.secretAccessor" \
    --project="$PROJECT_ID" \
    --quiet >/dev/null
  ok "Secret stored and access granted"
  GOOGLE_CREDS_SECRET="--set-secrets=/secrets/gcp-key.json=service-account-key:latest"
  GOOGLE_CREDS_ENV="GOOGLE_APPLICATION_CREDENTIALS=/secrets/gcp-key.json"
else
  warn "service-account-key.json not found — GOOGLE_APPLICATION_CREDENTIALS not set"
  GOOGLE_CREDS_SECRET=""
  GOOGLE_CREDS_ENV=""
fi

# ─── 6. Apply schema ──────────────────────────────────────────────────────────
step "Applying schema to Cloud SQL..."
PROXY_BIN="/tmp/cloud-sql-proxy"
if ! command -v psql &>/dev/null; then
  warn "psql not installed — skipping schema apply. Run it manually (see end of script)."
else
  if [ ! -f "$PROXY_BIN" ]; then
    echo "  Downloading Cloud SQL Auth Proxy..."
    curl -sL "https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.11.0/cloud-sql-proxy.linux.amd64" \
      -o "$PROXY_BIN" && chmod +x "$PROXY_BIN"
  fi
  "$PROXY_BIN" "${PROJECT_ID}:${REGION}:${SQL_INSTANCE}" --port=5433 &
  PROXY_PID=$!
  sleep 4
  PGPASSWORD="$DB_PASSWORD" psql \
    "host=127.0.0.1 port=5433 dbname=$DB_NAME user=$DB_USER sslmode=disable" \
    < "$SCRIPT_DIR/backend/schema.sql" \
    && ok "Schema applied" \
    || warn "Schema apply failed — may already be applied, or check errors above"
  kill "$PROXY_PID" 2>/dev/null || true
fi

# ─── 7. Build & deploy backend ────────────────────────────────────────────────
CLOUD_SQL_CONN="${PROJECT_ID}:${REGION}:${SQL_INSTANCE}"
DB_URL="postgresql://${DB_USER}:${DB_PASSWORD}@/${DB_NAME}?host=/cloudsql/${CLOUD_SQL_CONN}"

step "Building and pushing backend image..."
sudo docker build -t backend:latest "$SCRIPT_DIR/backend"
sudo docker tag backend:latest "${REGISTRY}/backend:latest"
sudo docker push "${REGISTRY}/backend:latest"
ok "Backend image pushed"

step "Deploying backend to Cloud Run..."
# shellcheck disable=SC2086
gcloud run deploy "$BACKEND_SERVICE" \
  --image="${REGISTRY}/backend:latest" \
  --platform=managed \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --add-cloudsql-instances="$CLOUD_SQL_CONN" \
  --set-env-vars="DATABASE_URL=${DB_URL},\
WHATSAPP_VERIFY_TOKEN=recruitflow_verify_123,\
WHATSAPP_API_TOKEN=${WHATSAPP_API_TOKEN:-},\
WHATSAPP_WABA_ID=${WHATSAPP_WABA_ID:-},\
MSG91_AUTH_KEY=${MSG91_AUTH_KEY:-},\
MSG91_CLIENT_WHATSAPP_NUMBER=${MSG91_CLIENT_WHATSAPP_NUMBER:-917383925646},\
MSG91_CANDIDATE_WHATSAPP_NUMBER=${MSG91_CANDIDATE_WHATSAPP_NUMBER:-919998910512},\
CANDIDATE_INTENT_TEMPLATE=candidate_share_client_jd,\
CLIENT_AGREEMENT_TEMPLATE=client_send_agreement,\
CLIENT_SLOT_TEMPLATE=client_select_interview_time_slot,\
CANDIDATE_INTENT_TEMPLATE_NAMESPACE=693b047c_7291_4380_a54d_3425d03fdb56,\
SCRAPER_JOB_PORTAL_URL=${SCRAPER_JOB_PORTAL_URL:-},\
SCRAPER_APOLLO_URL=${SCRAPER_APOLLO_URL:-},\
SCRAPER_GOOGLE_MAPS_URL=${SCRAPER_GOOGLE_MAPS_URL:-},\
SCRAPER_CUSTOM_URL=${SCRAPER_CUSTOM_URL:-},\
MATCHING_API_URL=${MATCHING_API_URL:-},\
FRONTEND_URL=https://${FRONTEND_DOMAIN},\
${GOOGLE_CREDS_ENV}" \
  ${GOOGLE_CREDS_SECRET} \
  --allow-unauthenticated \
  --port=8080 \
  --memory=512Mi \
  --min-instances=0 \
  --max-instances=5 \
  --quiet
ok "Backend deployed"

BACKEND_URL=$(gcloud run services describe "$BACKEND_SERVICE" \
  --platform=managed --region="$REGION" --project="$PROJECT_ID" \
  --format="value(status.url)")
echo "  Backend URL: $BACKEND_URL"

# ─── 8. Update backend CORS with known URLs ───────────────────────────────────
step "Updating backend CORS origins..."
gcloud run services update "$BACKEND_SERVICE" \
  --platform=managed \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --update-env-vars="ALLOWED_ORIGINS=https://${FRONTEND_DOMAIN}" \
  --quiet
ok "CORS updated"

# ─── 9. Build & deploy frontend ───────────────────────────────────────────────
step "Building and pushing frontend image (VITE_API_BASE_URL=${BACKEND_URL})..."
sudo docker build \
  --build-arg "VITE_API_BASE_URL=${BACKEND_URL}" \
  -t frontend:latest \
  "$SCRIPT_DIR/frontend"
sudo docker tag frontend:latest "${REGISTRY}/frontend:latest"
sudo docker push "${REGISTRY}/frontend:latest"
ok "Frontend image pushed"

step "Deploying frontend to Cloud Run..."
gcloud run deploy "$FRONTEND_SERVICE" \
  --image="${REGISTRY}/frontend:latest" \
  --platform=managed \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --allow-unauthenticated \
  --port=8080 \
  --memory=256Mi \
  --min-instances=0 \
  --max-instances=3 \
  --quiet
ok "Frontend deployed"

FRONTEND_RUN_URL=$(gcloud run services describe "$FRONTEND_SERVICE" \
  --platform=managed --region="$REGION" --project="$PROJECT_ID" \
  --format="value(status.url)")
echo "  Frontend Run URL: $FRONTEND_RUN_URL"

# ─── 10. Map custom domain ────────────────────────────────────────────────────
step "Mapping domain ${FRONTEND_DOMAIN} → ${FRONTEND_SERVICE}..."
gcloud beta run domain-mappings create \
  --service="$FRONTEND_SERVICE" \
  --domain="$FRONTEND_DOMAIN" \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --quiet 2>/dev/null && ok "Domain mapping created" || ok "Domain mapping already exists"

echo ""
step "Required DNS records for ${FRONTEND_DOMAIN}:"
gcloud beta run domain-mappings describe \
  --domain="$FRONTEND_DOMAIN" \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --format="table(status.resourceRecords[].name,status.resourceRecords[].type,status.resourceRecords[].rrdata)" \
  2>/dev/null || warn "DNS records not ready yet — run this after a minute:
  gcloud beta run domain-mappings describe --domain=${FRONTEND_DOMAIN} --region=${REGION} --project=${PROJECT_ID}"

# ─── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════════════${RESET}"
echo -e "${GREEN}  DEPLOYMENT COMPLETE${RESET}"
echo    "  Backend:        $BACKEND_URL"
echo    "  Frontend:       $FRONTEND_RUN_URL"
echo    "  Custom domain:  https://${FRONTEND_DOMAIN}"
echo    "  DB password:    $DB_PASSWORD"
echo -e "${BOLD}═══════════════════════════════════════════════════════════${RESET}"
echo ""
echo "Next steps:"
echo "  1. Add the DNS records above to your domain registrar for ${FRONTEND_DOMAIN}"
echo "  2. SSL certificate will auto-provision (takes ~15 min after DNS propagates)"
echo ""
if ! command -v psql &>/dev/null; then
  echo "  3. Apply the schema manually:"
  echo "     Install psql, then run:"
  echo "     DB_PASSWORD=${DB_PASSWORD} bash -c '"'
  echo '       /tmp/cloud-sql-proxy '"${PROJECT_ID}:${REGION}:${SQL_INSTANCE}"' --port=5433 &'
  echo '       sleep 4'
  echo "       PGPASSWORD=\$DB_PASSWORD psql \"host=127.0.0.1 port=5433 dbname=${DB_NAME} user=${DB_USER} sslmode=disable\" < backend/schema.sql"
  echo "       kill %1"
  echo "     '"
fi
