#!/bin/bash
set -e

# Set up the environment like the original entrypoint
cd /aviator_adt
export VIRTUAL_ENV=/aviator_adt/.venv
export PATH="$VIRTUAL_ENV/bin:$PATH"

echo "🔍 Waiting for Vault AppRole credentials..."

# Wait for the credentials file to be available
CREDS_FILE="/shared/vault-approle-creds.env"
TIMEOUT=60
COUNTER=0

while [ ! -f "$CREDS_FILE" ] && [ $COUNTER -lt $TIMEOUT ]; do
    echo "⏳ Waiting for vault credentials file... ($COUNTER/$TIMEOUT)"
    sleep 1
    COUNTER=$((COUNTER + 1))
done

if [ ! -f "$CREDS_FILE" ]; then
    echo "❌ Timeout waiting for vault credentials file: $CREDS_FILE"
    exit 1
fi

echo "✅ Found vault credentials file, loading..."

# Source the credentials file to get VAULT_ROLE_ID and VAULT_SECRET_ID
set -a  # automatically export all variables
source "$CREDS_FILE"
set +a

echo "🔑 Loaded vault credentials:"
echo "   VAULT_ROLE_ID=${VAULT_ROLE_ID}"
echo "   VAULT_SECRET_ID=${VAULT_SECRET_ID:0:8}..." # Only show first 8 chars for security

# Build the VAULT_CONFIG JSON with the loaded credentials
export VAULT_CONFIG="{\"apiVersion\":\"v2\",\"endpoint\":\"http://vault:8200\",\"approle\":{\"roleId\":\"${VAULT_ROLE_ID}\",\"secretId\":\"${VAULT_SECRET_ID}\"}}"

echo "🚀 Starting aviator service with dynamic vault config..."

# Execute the original command - if "app" is passed, run "aviator" instead
if [ "$1" = "app" ]; then
    exec aviator
elif [ $# -eq 0 ]; then
    exec aviator
else
    exec "$@"
fi