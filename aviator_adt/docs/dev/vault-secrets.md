# HashiCorp Vault Integration

This document describes how to configure the Content Aviator ADT application to use HashiCorp Vault for secret management.

## Quick Start with Skaffold

The fastest way to test Vault integration is using the provided Vault profile:

### Prerequisites
1. Docker Desktop with Kubernetes enabled, OR a real Kubernetes cluster
2. Vault backend running (automatically deployed with the profile)

### Deploy Vault with Skaffold
```bash
# Deploy Vault backend first
skaffold -f skaffold-backend.yaml run -p vault

# Port Forward the vault service
kubectl port-forward services/aviator-backend-vault 8200:8200 -n <namespace>

# Configure Kubernetes authentication
scripts/vault/setup-k8s-auth.sh

# Deploy Aviator with Vault authentication
skaffold run -p vault
```

The vault profile automatically:
- ✅ Deploys Vault in development mode
- ✅ Creates necessary secrets and policies  
- ✅ Sets up RBAC permissions (real clusters only)

After deployment, run the **setup script** to configure Kubernetes authentication:
- ✅ **Smart deployment detection** (only runs when Vault is deployed)
- ✅ **Automatic namespace detection** from `skaffold.env`
- ✅ **Configures Kubernetes authentication method**
- ✅ **Creates policies and roles automatically**

> **Note**: The setup script will automatically detect if Vault is deployed and configure Kubernetes authentication accordingly. It can be run safely multiple times.

## Configuration

To enable Vault secret management, set the following environment variables:

### Basic Configuration

```bash
# Set the secrets manager provider to vault
SECRETS_MANAGER=vault

# Vault server URL
VAULT_URL=https://your-vault-server.com:8200

# Vault configuration (JSON string)
VAULT_CONFIG='{"token":"your-vault-token","apiVersion":"v1","pathPrefix":"secret"}'

# Secret keys in Vault (all optional - falls back to environment variables if not set)
SECRETS_PGVECTOR_PASSWORD_KEY=myapp/database::password
SECRETS_BROKER_PASSWORD_KEY=myapp/broker::password
SECRETS_OPENAI_API_KEY=myapp/openai::api_key
SECRETS_ANTHROPIC_API_KEY=myapp/anthropic::api_key
SECRETS_MISTRAL_API_KEY=myapp/mistral::api_key
SECRETS_AWS_BEDROCK_ACCESS_KEY=myapp/aws::bedrock_access_key
SECRETS_AWS_BEDROCK_SECRET_KEY=myapp/aws::bedrock_secret_key
```

## Secret Types

The application supports the following managed secrets via Vault:

| Secret Type | Env Variable | Secret Key Setting | When Used |
|-------------|--------------|-------------------|-----------|
| PostgreSQL Database Password | `POSTGRES_PASSWORD` | `SECRETS_PGVECTOR_PASSWORD_KEY` | Always (required for database connection) |
| Message Broker Password | `BROKER_PASSWORD` | `SECRETS_BROKER_PASSWORD_KEY` | When constructing broker connection (RabbitMQ, Redis, Pub/Sub) |
| OpenAI API Key | `OPENAI_API_KEY` | `SECRETS_OPENAI_API_KEY` | When `LLM_PROVIDER=openai` |
| Anthropic API Key | `ANTHROPIC_API_KEY` | `SECRETS_ANTHROPIC_API_KEY` | When `LLM_PROVIDER=anthropic` |
| Mistral API Key | `MISTRAL_API_KEY` | `SECRETS_MISTRAL_API_KEY` | When `LLM_PROVIDER=mistral` |
| AWS Bedrock Access Key | `AWS_BEDROCK_ACCESS_KEY` | `SECRETS_AWS_BEDROCK_ACCESS_KEY` | When `LLM_PROVIDER=aws_bedrock` |
| AWS Bedrock Secret Key | `AWS_BEDROCK_SECRET_KEY` | `SECRETS_AWS_BEDROCK_SECRET_KEY` | When `LLM_PROVIDER=aws_bedrock` |

**All secret keys are optional.** If a secret key is not configured, the application falls back to the corresponding environment variable.

## Authentication Methods

The Vault provider supports multiple authentication methods through the `VAULT_CONFIG` JSON configuration:

### 1. Token Authentication

```json
{
  "token": "your-vault-token",
  "apiVersion": "v1",
  "pathPrefix": "secret"
}
```

### 2. AppRole Authentication

```json
{
  "approle": {
    "roleId": "your-role-id",
    "secretId": "your-secret-id"
  },
  "apiVersion": "v2",
  "pathPrefix": "secret"
}
```

### 3. Username/Password Authentication

```json
{
  "userpass": {
    "username": "your-username",
    "password": "your-password"
  },
  "apiVersion": "v1",
  "pathPrefix": "secret"
}
```

### 4. Kubernetes Authentication

```json
{
  "kubernetes": {
    "appNameEnvVar": "APP_NAME",
    "jwtEnvVar": "APP_SVC_ACCT_SECRET_TOKEN"
  },
  "apiVersion": "v2",
  "pathPrefix": "secret"
}
```



#### Helm Chart RBAC Configuration

The Vault Helm chart includes automatic RBAC configuration:

**Enable in values:**
```yaml
# helm/site-specific-values/backend/vault.yaml
vault:
  enabled: true
  rbac:
    create: true  # Creates ClusterRole and ClusterRoleBinding automatically
```

**Default behavior:**
- ❌ **Disabled by default** (safe for development)
- ✅ **Auto-enabled** when using `helm/site-specific-values/backend/vault.yaml`
- ✅ **Namespace-aware** (works with `SKAFFOLD_NAMESPACE`)

#### Environment Compatibility

| Environment | Kubernetes Auth | RBAC Required | Notes |
|-------------|----------------|---------------|--------|
| Docker Desktop | ⚠️ Limited | ❌ No | Falls back to env vars due to K8s API limitations |
| Real K8s Clusters | ✅ Full Support | ✅ Yes | Requires RBAC permissions for token validation |
| Minikube | ✅ Full Support | ✅ Yes | Same as real clusters |

## KV Engine Versions

The Vault provider supports both KV v1 and v2 engines:

- **v1**: Direct key-value storage
- **v2**: Versioned key-value storage (default)


## Secret Key Format

The Vault provider supports extracting specific fields from JSON secrets using the `::` separator:

### Examples

```bash
# Get entire secret (returns JSON if multiple fields)
SECRETS_PGVECTOR_PASSWORD_KEY=myapp/database

# Get specific field from JSON secret
SECRETS_PGVECTOR_PASSWORD_KEY=myapp/database::password

# Get nested field from JSON secret
SECRETS_PGVECTOR_PASSWORD_KEY=myapp/config::database::password
```

### Alternative: Manual Setup (If Needed)

If you need to run the authentication setup manually or troubleshoot:

If you need to run the authentication setup manually:

```bash
# Ensure Vault is accessible (port-forward if needed)
kubectl port-forward deployment/aviator-backend-vault 8200:8200 &

# Run the setup script manually
scripts/vault/setup-k8s-auth.sh
```


### Script Environment Variables

The setup script supports these environment variables for customization:

```bash
VAULT_URL=http://localhost:8200     # Vault server URL
VAULT_TOKEN=password                # Vault token for configuration
MAX_WAIT_SECONDS=120               # Maximum wait for Vault readiness
WAIT_INTERVAL=5                    # Wait interval between checks
```

## Deploy with Docker Compose

For local development and testing, you can use Docker Compose to run Aviator with Vault integration. This setup allows you to test all supported authentication methods and KV engine versions.

### Prerequisites

1. **Docker and Docker Compose installed**
2. **Google Service Account credentials** - Place your `otl-cs-csai.json` (or similar) file in the project root
3. **Environment configuration** - Set `GOOGLE_APPLICATION_CREDENTIALS=./your-credentials.json` in your `.env` file

### Quick Start

1. **Start the Vault-enabled stack:**
   ```bash
   docker-compose -f docker-compose.dev.vault.yml up -d
   ```

   This automatically:
   - ✅ Starts Vault server in development mode
   - ✅ Initializes both KV v1 and KV v2 engines
   - ✅ Sets up all authentication methods (token, userpass, approle)
   - ✅ Creates test secrets in both KV engines
   - ✅ Configures Aviator to use Vault for secret management

2. **Verify services are running:**
   ```bash
   docker-compose -f docker-compose.dev.vault.yml ps
   ```

3. **Check Vault initialization logs:**
   ```bash
   docker-compose -f docker-compose.dev.vault.yml logs vault-init
   ```

### Testing Different Configurations

The `docker-compose.dev.vault.yml` file includes commented configuration options for testing different authentication methods and KV engine versions. You can modify the file to test specific scenarios:

#### Test KV Engine Version 1 with Token Authentication

**Configuration in `docker-compose.dev.vault.yml`:**
```yaml
services:
  aviator:
    environment:
      # VERSION 1 - Uncomment these lines for KV v1 testing
      VAULT_CONFIG: '{"apiVersion":"v1","endpoint":"http://vault:8200","token":"password"}'
      SECRETS_PGVECTOR_PASSWORD_KEY: 'kv1/path_name::DATABASE_PASSWORD'
```

**Steps:**
1. Edit `docker-compose.dev.vault.yml`
2. Uncomment the KV v1 lines and comment out the KV v2 lines
3. Restart the aviator service:
   ```bash
   docker-compose -f docker-compose.dev.vault.yml restart aviator
   ```

#### Test KV Engine Version 2 with Token Authentication (Default)

**Configuration in `docker-compose.dev.vault.yml`:**
```yaml
services:
  aviator:
    environment:
      # VERSION 2 - KV v2 with token auth (default configuration)
      SECRETS_PGVECTOR_PASSWORD_KEY: 'secret/data/path_name::DATABASE_PASSWORD'
      VAULT_CONFIG: '{"apiVersion":"v2","endpoint":"http://vault:8200","token":"password"}'
```

This is the default configuration and should work out of the box.

#### Test KV Engine Version 2 with Username/Password Authentication

**Configuration in `docker-compose.dev.vault.yml`:**
```yaml
services:
  aviator:
    environment:
      # VERSION 2 - KV v2 with username/password auth
      SECRETS_PGVECTOR_PASSWORD_KEY: 'secret/data/path_name::DATABASE_PASSWORD'
      VAULT_CONFIG: '{"apiVersion":"v2","endpoint":"http://vault:8200","userpass":{"username":"testuser", "password":"testpass"}}'
```

**Steps:**
1. Edit `docker-compose.dev.vault.yml` 
2. Comment out the token-based `VAULT_CONFIG` line
3. Uncomment the userpass-based `VAULT_CONFIG` line
4. Restart the aviator service:
   ```bash
   docker-compose -f docker-compose.dev.vault.yml restart aviator
   ```

#### Test KV Engine Version 2 with AppRole Authentication

**Configuration in `docker-compose.dev.vault.yml`:**
```yaml
services:
  aviator:
    # Uncomment the custom entrypoint for AppRole auth
    entrypoint: ["/usr/local/bin/aviator-vault-entrypoint.sh"]
    environment:
      SECRETS_PGVECTOR_PASSWORD_KEY: 'secret/data/path_name::DATABASE_PASSWORD'
      # VAULT_CONFIG will be set dynamically by entrypoint script
    volumes:
      - vault_shared:/shared:ro
      # Uncomment this volume for AppRole auth
      - ./docker_compose_scripts/aviator-vault-entrypoint.sh:/usr/local/bin/aviator-vault-entrypoint.sh:ro
```

**Steps:**
1. Edit `docker-compose.dev.vault.yml`
2. Uncomment the `entrypoint` line for the aviator service
3. Uncomment the volume mount for `aviator-vault-entrypoint.sh`
4. Comment out any explicit `VAULT_CONFIG` environment variable (it will be set dynamically)
5. Restart the stack to pick up the new configuration:
   ```bash
   docker-compose -f docker-compose.dev.vault.yml down
   docker-compose -f docker-compose.dev.vault.yml up -d
   ```

### Verifying Authentication Methods

#### 1. Access Vault UI
```bash
# Vault UI is available at http://localhost:8200
# Login with token: password
open http://localhost:8200
```

#### 2. Test Secret Access
```bash
# Test KV v1 secret access
curl -H "X-Vault-Token: password" http://localhost:8200/v1/kv1/path_name

# Test KV v2 secret access  
curl -H "X-Vault-Token: password" http://localhost:8200/v1/secret/data/path_name
```

#### 3. Test Authentication Methods
```bash
# Test username/password authentication
curl -X POST http://localhost:8200/v1/auth/userpass/login/testuser \
  -d '{"password":"testpass"}'

# Test AppRole authentication (get credentials from vault-init logs)
ROLE_ID="<role-id-from-logs>"
SECRET_ID="<secret-id-from-logs>"
curl -X POST http://localhost:8200/v1/auth/approle/login \
  -d "{\"role_id\":\"$ROLE_ID\",\"secret_id\":\"$SECRET_ID\"}"
```

### Troubleshooting

#### Check Service Health
```bash
# Check all service status
docker-compose -f docker-compose.dev.vault.yml ps

# Check specific service logs
docker-compose -f docker-compose.dev.vault.yml logs aviator
docker-compose -f docker-compose.dev.vault.yml logs vault
docker-compose -f docker-compose.dev.vault.yml logs vault-init
```

#### Verify Database Connection
```bash
# Check if Aviator can connect to PostgreSQL using Vault-retrieved password
docker-compose -f docker-compose.dev.vault.yml exec aviator \
  python -c "import os; print('POSTGRES_PASSWORD from Vault:', os.getenv('POSTGRES_PASSWORD', 'NOT_SET'))"
```

#### Reset Vault State
```bash
# Stop services and remove volumes to start fresh
docker-compose -f docker-compose.dev.vault.yml down -v
docker-compose -f docker-compose.dev.vault.yml up -d
```

### Available Test Credentials

The vault initialization script creates the following test credentials:

| Authentication Method | Credentials | KV Engine Support |
|-----------------------|-------------|-------------------|
| **Token** | `password` | v1 and v2 |
| **Username/Password** | `testuser` / `testpass` | v1 and v2 |
| **AppRole** | Dynamic (see vault-init logs) | v1 and v2 |

### Secret Paths for Testing

| KV Engine | Secret Path | Environment Variable |
|-----------|-------------|---------------------|
| **KV v1** | `kv1/path_name::DATABASE_PASSWORD` | `SECRETS_PGVECTOR_PASSWORD_KEY='kv1/path_name::DATABASE_PASSWORD'` |
| **KV v2** | `secret/data/path_name::DATABASE_PASSWORD` | `SECRETS_PGVECTOR_PASSWORD_KEY='secret/data/path_name::DATABASE_PASSWORD'` |

Both paths contain the PostgreSQL password (`postgres`) for testing database connectivity.



