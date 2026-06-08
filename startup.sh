#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# startup.sh — Azure App Service startup command
#
# Set this as the startup command in Azure Portal:
#   App Service → Configuration → General Settings → Startup Command
#   Value: bash startup.sh
#
# Azure App Service routes external traffic to port 8000 by default.
# HTTPS is handled by the platform — enable "HTTPS Only" in TLS/SSL settings.
# ─────────────────────────────────────────────────────────────────────────────

set -e

echo "Starting BRD Agent..."
echo "Python: $(python --version)"
echo "Port: ${PORT:-8000}"

streamlit run UI/streamlit_app.py \
    --server.port       "${PORT:-8000}" \
    --server.address    "0.0.0.0" \
    --server.headless   true \
    --server.enableCORS false \
    --server.enableXsrfProtection true \
    --server.maxUploadSize 20
