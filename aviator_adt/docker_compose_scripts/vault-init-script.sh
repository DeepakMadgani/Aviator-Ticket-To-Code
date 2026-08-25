#!/bin/bash
set -e

# Vault initialization script - sets up all authentication methods and secrets
# This script consolidates all vault setup tasks for development testing

VAULT_URL="http://vault:8200"
VAULT_TOKEN="password"

echo "==================================================================================="
echo "Starting comprehensive Vault initialization..."
echo "==================================================================================="

# Function to setup custom policy for secret access
setup_secret_policy() {
    echo ""
    echo ">>> Setting up custom policy for secret access..."
    
    # Create a policy that allows reading from both KV v1 and v2 engines
    POLICY_JSON='{
        "policy": "path \"kv1/*\" {\n  capabilities = [\"read\", \"list\"]\n}\n\npath \"secret/data/*\" {\n  capabilities = [\"read\", \"list\"]\n}\n\npath \"secret/metadata/*\" {\n  capabilities = [\"read\", \"list\"]\n}"
    }'
    
    echo "Creating 'secrets-read' policy..."
    curl -f -X PUT ${VAULT_URL}/v1/sys/policies/acl/secrets-read \
      -H "X-Vault-Token: ${VAULT_TOKEN}" \
      -H "Content-Type: application/json" \
      -d "$POLICY_JSON" || {
      echo "Policy creation failed!"
      exit 1
    }
    
    echo "✅ Custom policy 'secrets-read' created successfully!"
}

# Function to setup KV engines and base secrets
setup_kv_engines() {
    echo ""
    echo ">>> Setting up KV engines and base secrets..."
    
    # Enable KV secrets engine (v1) if not already enabled
    echo "Creating kv1 mount..."
    curl -f -X PUT ${VAULT_URL}/v1/sys/mounts/kv1 \
      -H "X-Vault-Token: ${VAULT_TOKEN}" \
      -H "Content-Type: application/json" \
      -d '{"type":"kv","options":{"version":"1"}}' || {
      echo "Mount creation failed or already exists, continuing..."
    }
    
    # Enable KV secrets engine (v2) at default secret mount if not already enabled
    echo "Creating secret mount (v2)..."
    curl -f -X PUT ${VAULT_URL}/v1/sys/mounts/secret \
      -H "X-Vault-Token: ${VAULT_TOKEN}" \
      -H "Content-Type: application/json" \
      -d '{"type":"kv-v2"}' || {
      echo "v2 mount creation failed or already exists, continuing..."
    }
    
    # Write the database and broker password secrets to KV v1
    echo "Writing database and broker password secrets to KV v1..."
    curl -f -X PUT ${VAULT_URL}/v1/kv1/path_name \
      -H "X-Vault-Token: ${VAULT_TOKEN}" \
      -H "Content-Type: application/json" \
      -d '{"DATABASE_PASSWORD":"postgres","BROKER_PASSWORD":"admin_pass"}' || {
      echo "v1 secret write failed!"
      exit 1
    }
    
    # Write the database and broker password secrets to KV v2
    echo "Writing database and broker password secrets to KV v2..."
    curl -f -X PUT ${VAULT_URL}/v1/secret/data/path_name \
      -H "X-Vault-Token: ${VAULT_TOKEN}" \
      -H "Content-Type: application/json" \
      -d '{"data":{"DATABASE_PASSWORD":"postgres","BROKER_PASSWORD":"admin_pass"}}' || {
      echo "v2 secret write failed!"
      exit 1
    }
    
    # Verify the v1 secret was written
    echo "Verifying v1 secret..."
    curl -f -H "X-Vault-Token: ${VAULT_TOKEN}" ${VAULT_URL}/v1/kv1/path_name || {
      echo "v1 secret verification failed!"
      exit 1
    }
    
    # Verify the v2 secret was written
    echo "Verifying v2 secret..."
    curl -f -H "X-Vault-Token: ${VAULT_TOKEN}" ${VAULT_URL}/v1/secret/data/path_name || {
      echo "v2 secret verification failed!"
      exit 1
    }
    
    echo "✅ KV engines and base secrets configured successfully!"
}

# Function to setup AppRole authentication
setup_approle_auth() {
    echo ""
    echo ">>> Setting up AppRole authentication..."
    
    # Enable AppRole auth method
    curl -f -X POST ${VAULT_URL}/v1/sys/auth/approle \
      -H "X-Vault-Token: ${VAULT_TOKEN}" \
      -H "Content-Type: application/json" \
      -d '{"type":"approle"}' || {
      echo "AppRole auth method creation failed or already exists, continuing..."
    }
    
    # Create AppRole role with policies
    echo "Creating AppRole role..."
    curl -f -X POST ${VAULT_URL}/v1/auth/approle/role/test-role \
      -H "X-Vault-Token: ${VAULT_TOKEN}" \
      -H "Content-Type: application/json" \
      -d '{"token_policies":["default","secrets-read"],"token_ttl":"1h","token_max_ttl":"4h"}' || {
      echo "AppRole role creation failed!"
      exit 1
    }
    
    # Get and display role-id for reference
    echo "Getting role-id..."
    ROLE_ID=$(curl -f ${VAULT_URL}/v1/auth/approle/role/test-role/role-id \
      -H "X-Vault-Token: ${VAULT_TOKEN}" | grep -o '"role_id":"[^"]*"' | cut -d'"' -f4)
    echo "Role ID: $ROLE_ID"
    
    # Generate and display secret-id for reference
    echo "Generating secret-id..."
    SECRET_ID=$(curl -f -X POST ${VAULT_URL}/v1/auth/approle/role/test-role/secret-id \
      -H "X-Vault-Token: ${VAULT_TOKEN}" | grep -o '"secret_id":"[^"]*"' | cut -d'"' -f4)
    echo "Secret ID: $SECRET_ID"
    
    # Write credentials to shared file for aviator service
    echo "Writing AppRole credentials to shared file..."
    cat > /shared/vault-approle-creds.env << EOF
VAULT_ROLE_ID=$ROLE_ID
VAULT_SECRET_ID=$SECRET_ID
EOF
    
    echo "✅ AppRole authentication configured successfully!"
    echo "📋 Test config: {\"apiVersion\":\"v2\",\"endpoint\":\"${VAULT_URL}\",\"approle\":{\"roleId\":\"$ROLE_ID\",\"secretId\":\"$SECRET_ID\"}}"    echo "📁 Credentials written to /shared/vault-approle-creds.env"
}

# Function to setup Username/Password authentication
setup_userpass_auth() {
    echo ""
    echo ">>> Setting up Username/Password authentication..."
    
    # Enable userpass auth method
    curl -f -X POST ${VAULT_URL}/v1/sys/auth/userpass \
      -H "X-Vault-Token: ${VAULT_TOKEN}" \
      -H "Content-Type: application/json" \
      -d '{"type":"userpass"}' || {
      echo "Userpass auth method creation failed or already exists, continuing..."
    }
    
    # Create test user
    echo "Creating test user..."
    curl -f -X POST ${VAULT_URL}/v1/auth/userpass/users/testuser \
      -H "X-Vault-Token: ${VAULT_TOKEN}" \
      -H "Content-Type: application/json" \
      -d '{"password":"testpass","policies":["default","secrets-read"]}' || {
      echo "Test user creation failed!"
      exit 1
    }
    
    # Verify user can authenticate
    echo "Testing user authentication..."
    AUTH_RESPONSE=$(curl -f -X POST ${VAULT_URL}/v1/auth/userpass/login/testuser \
      -H "Content-Type: application/json" \
      -d '{"password":"testpass"}')
    
    if echo "$AUTH_RESPONSE" | grep -q "client_token"; then
      echo "User authentication test successful!"
      
      # Extract the user token and test secret access
      USER_TOKEN=$(echo "$AUTH_RESPONSE" | grep -o '"client_token":"[^"]*"' | cut -d'"' -f4)
      echo "Testing secret access with user token..."
      
      # Test v1 secret access
      if curl -f -s -H "X-Vault-Token: $USER_TOKEN" ${VAULT_URL}/v1/kv1/path_name > /dev/null 2>&1; then
        echo "✅ User can access KV v1 secrets"
      else
        echo "❌ User cannot access KV v1 secrets"
      fi
      
      # Test v2 secret access  
      if curl -f -s -H "X-Vault-Token: $USER_TOKEN" ${VAULT_URL}/v1/secret/data/path_name > /dev/null 2>&1; then
        echo "✅ User can access KV v2 secrets"
      else
        echo "❌ User cannot access KV v2 secrets"
      fi
    else
      echo "User authentication test failed!"
      exit 1
    fi
    
    echo "✅ Username/Password authentication configured successfully!"
    echo "📋 Test config: {\"apiVersion\":\"v2\",\"endpoint\":\"${VAULT_URL}\",\"userpass\":{\"username\":\"testuser\",\"password\":\"testpass\"}}"
}



# Function to display summary
display_summary() {
    echo ""
    echo "==================================================================================="
    echo "🎉 Vault initialization completed successfully!"
    echo "==================================================================================="
    echo ""
    echo "Available Authentication Methods:"
    echo ""
    echo "1. 🔑 TOKEN (default):"
    echo "   VAULT_CONFIG='{\"apiVersion\":\"v2\",\"endpoint\":\"${VAULT_URL}\",\"token\":\"${VAULT_TOKEN}\"}'"
    echo ""
    echo "2. ⚡ APPROLE:"
    echo "   Check logs above for generated role-id and secret-id"
    echo ""
    echo "3. 👤 USERPASS:"
    echo "   VAULT_CONFIG='{\"apiVersion\":\"v2\",\"endpoint\":\"${VAULT_URL}\",\"userpass\":{\"username\":\"testuser\",\"password\":\"testpass\"}}'"
    echo ""
    echo "🗂️  Available Secrets (accessible by all auth methods):"
    echo "   • KV v1: kv1/path_name::DATABASE_PASSWORD"
    echo "   • KV v1: kv1/path_name::BROKER_PASSWORD"
    echo "   • KV v2: secret/data/path_name::DATABASE_PASSWORD"
    echo "   • KV v2: secret/data/path_name::BROKER_PASSWORD"
    echo ""
    echo "🔐 Policy Configuration:"
    echo "   • All authentication methods have 'secrets-read' policy"
    echo "   • This policy grants read access to both KV v1 and v2 engines"
    echo "   • Users can access secrets from either engine version"
    echo ""
    echo "==================================================================================="
}

# Main execution
main() {
    setup_secret_policy
    setup_kv_engines
    setup_approle_auth
    setup_userpass_auth
    display_summary
}

# Run the main function
main "$@"