#!/bin/bash
set -e

# Vault Kubernetes Authentication Setup for Skaffold Deployment
# This script is automatically run after Vault deployment to configure Kubernetes authentication

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Configuration defaults
VAULT_URL="${VAULT_URL:-http://localhost:8200}"
VAULT_TOKEN="${VAULT_TOKEN:-password}"
MAX_WAIT_SECONDS="${MAX_WAIT_SECONDS:-120}"
WAIT_INTERVAL="${WAIT_INTERVAL:-5}"

# Source skaffold.env from project root to get the namespace
if [[ -f "${PROJECT_ROOT}/skaffold.env" ]]; then
    source "${PROJECT_ROOT}/skaffold.env"
    echo "Using namespace from skaffold.env: ${SKAFFOLD_NAMESPACE:-default}"
    NAMESPACE="${SKAFFOLD_NAMESPACE:-default}"
else
    echo "skaffold.env not found, using current kubectl context"
    NAMESPACE=$(kubectl config view --minify -o jsonpath='{..namespace}' 2>/dev/null || echo "default")
fi

echo "==================================================================================="
echo "🔧 Setting up Kubernetes authentication in Vault..."
echo "==================================================================================="
echo "Target namespace: ${NAMESPACE}"
echo "Vault URL: ${VAULT_URL}"
echo ""

# Function to wait for Vault to be ready
wait_for_vault() {
    echo "Waiting for Vault to be ready..."
    local elapsed=0
    
    while [ $elapsed -lt $MAX_WAIT_SECONDS ]; do
        if curl -s ${VAULT_URL}/v1/sys/health > /dev/null 2>&1; then
            echo "✅ Vault is accessible"
            return 0
        fi
        
        echo "⏳ Waiting for Vault... (${elapsed}s/${MAX_WAIT_SECONDS}s)"
        sleep $WAIT_INTERVAL
        elapsed=$((elapsed + WAIT_INTERVAL))
    done
    
    echo "❌ Timeout waiting for Vault to be ready"
    return 1
}

# Function to check if auth method exists
check_auth_method_exists() {
    local auth_type=$1
    curl -s -f ${VAULT_URL}/v1/sys/auth -H "X-Vault-Token: ${VAULT_TOKEN}" | grep -q "\"${auth_type}/\""
}

# Function to setup secrets policy
setup_secrets_policy() {
    echo "Creating secrets-read policy..."
    local policy_json='{
        "policy": "path \"kv1/*\" {\n  capabilities = [\"read\", \"list\"]\n}\n\npath \"secret/data/*\" {\n  capabilities = [\"read\", \"list\"]\n}\n\npath \"secret/metadata/*\" {\n  capabilities = [\"read\", \"list\"]\n}"
    }'
    
    curl -s -X PUT ${VAULT_URL}/v1/sys/policies/acl/secrets-read \
      -H "X-Vault-Token: ${VAULT_TOKEN}" \
      -H "Content-Type: application/json" \
      -d "$policy_json" > /dev/null || {
      echo "⚠️  Policy creation failed or already exists"
    }
}

# Function to setup KV mounts and secrets
setup_kv_secrets() {
    echo "Setting up KV mounts and secrets..."
    
    # Create KV v1 mount
    curl -s -X PUT ${VAULT_URL}/v1/sys/mounts/kv1 \
      -H "X-Vault-Token: ${VAULT_TOKEN}" \
      -H "Content-Type: application/json" \
      -d '{"type":"kv","options":{"version":"1"}}' > /dev/null || true
    
    # Write database password to KV v1
    curl -s -X PUT ${VAULT_URL}/v1/kv1/path_name \
      -H "X-Vault-Token: ${VAULT_TOKEN}" \
      -H "Content-Type: application/json" \
      -d '{"DATABASE_PASSWORD":"1-TERRIBLE-password!"}' > /dev/null || {
      echo "⚠️  Failed to write KV v1 secret"
    }
    
    # Write database password to KV v2 (secret mount should exist by default)
    curl -s -X PUT ${VAULT_URL}/v1/secret/data/path_name \
      -H "X-Vault-Token: ${VAULT_TOKEN}" \
      -H "Content-Type: application/json" \
      -d '{"data":{"DATABASE_PASSWORD":"1-TERRIBLE-password!"}}' > /dev/null || {
      echo "⚠️  Failed to write KV v2 secret"
    }
}

# Function to setup Kubernetes auth
setup_kubernetes_auth() {
    echo "Configuring Kubernetes authentication..."
    
    # Check if Kubernetes auth already exists
    if check_auth_method_exists "kubernetes"; then
        echo "✅ Kubernetes auth method already exists"
    else
        echo "Enabling Kubernetes auth method..."
        curl -s -X POST ${VAULT_URL}/v1/sys/auth/kubernetes \
          -H "X-Vault-Token: ${VAULT_TOKEN}" \
          -H "Content-Type: application/json" \
          -d '{"type":"kubernetes"}' > /dev/null || {
          echo "❌ Failed to enable Kubernetes auth method"
          return 1
        }
        echo "✅ Kubernetes auth method enabled"
    fi
    
    # Configure Kubernetes auth backend
    curl -s -X POST ${VAULT_URL}/v1/auth/kubernetes/config \
      -H "X-Vault-Token: ${VAULT_TOKEN}" \
      -H "Content-Type: application/json" \
      -d '{"kubernetes_host": "https://kubernetes.default.svc.cluster.local", "disable_iss_validation": true}' > /dev/null || {
      echo "❌ Failed to configure Kubernetes auth"
      return 1
    }
    echo "✅ Kubernetes auth configured"
    
    # Create aviator role
    echo "Creating Kubernetes role 'aviator' for namespace '${NAMESPACE}'..."
    curl -s -X POST ${VAULT_URL}/v1/auth/kubernetes/role/aviator \
      -H "X-Vault-Token: ${VAULT_TOKEN}" \
      -H "Content-Type: application/json" \
      -d "{\"bound_service_account_names\":[\"*\"],\"bound_service_account_namespaces\":[\"${NAMESPACE}\"],\"policies\":[\"default\",\"secrets-read\"],\"ttl\":\"1h\"}" > /dev/null || {
      echo "⚠️  Failed to create Kubernetes role"
      return 1
    }
    
    # Verify role creation
    if curl -s -f ${VAULT_URL}/v1/auth/kubernetes/role/aviator \
       -H "X-Vault-Token: ${VAULT_TOKEN}" | grep -q "bound_service_account_names"; then
        echo "✅ Kubernetes role 'aviator' created successfully"
    else
        echo "⚠️  Could not verify Kubernetes role creation"
    fi
}

# Main execution
main() {
    # Wait for Vault to be ready
    if ! wait_for_vault; then
        echo "❌ Cannot proceed without Vault access"
        exit 1
    fi
    
    # Setup components
    setup_secrets_policy
    setup_kv_secrets
    setup_kubernetes_auth
    
    echo ""
    echo "==================================================================================="
    echo "🎉 Vault Kubernetes authentication setup completed!"
    echo "==================================================================================="
    echo ""
    echo "✅ Configuration Summary:"
    echo "  • Namespace: ${NAMESPACE}"
    echo "  • Kubernetes auth method: Enabled"
    echo "  • Role 'aviator': Created with wildcard service account binding"
    echo "  • Secrets policy: Created for KV v1 and v2 access"
    echo "  • Test secrets: Written to kv1/path_name and secret/path_name"
    echo ""
    echo "🔐 RBAC Note:"
    echo "  RBAC permissions are automatically configured via Helm chart"
    echo "  when using helm/site-specific-values/backend/vault.yaml"
    echo ""
    echo "🚀 Vault is ready for Kubernetes authentication!"
    echo "=================================================================================="
}

# Run main function with error handling
if ! main; then
    echo "❌ Setup failed. Check Vault logs and configuration."
    exit 1
fi