# Google Cloud Secret Manager Integration

This document describes how to configure and use Google Cloud Secret Manager for retrieving sensitive configuration values, specifically the PostgreSQL password.

## Overview

The Content Aviator ADT supports two methods for managing secrets:

1. **Environment Variables** (default) - Traditional approach using environment variables
2. **Google Cloud Secret Manager** - Secure approach using Google Cloud's managed secrets service

## Configuration

### Environment Variables

To use Google Cloud Secret Manager, configure the following environment variables:

```bash
# Enable Google Cloud Secret Manager
SECRETS_MANAGER=google

# (Optional) Google Cloud project ID - defaults to GOOGLE_CLOUD_PROJECT or project from credentials
GOOGLE_CLOUD_PROJECT=my-project-id

# Google Cloud credentials (required)
GOOGLE_APPLICATION_CREDENTIALS=./path/to/service-account-key.json

# Secret Manager keys for various credentials
# Database Password
SECRETS_PGVECTOR_PASSWORD_KEY=postgres-password

# Message Broker Password (optional)
SECRETS_BROKER_PASSWORD_KEY=broker-password

# Provider API Keys (optional, based on LLM_PROVIDER)
SECRETS_OPENAI_API_KEY=openai-api-key
SECRETS_ANTHROPIC_API_KEY=anthropic-api-key
SECRETS_MISTRAL_API_KEY=mistral-api-key
SECRETS_AWS_BEDROCK_ACCESS_KEY=aws-bedrock-access-key
SECRETS_AWS_BEDROCK_SECRET_KEY=aws-bedrock-secret-key

# All secret keys support both simple and JSON field extraction formats:
# - Simple: SECRETS_PGVECTOR_PASSWORD_KEY=postgres-password
# - JSON field: SECRETS_PGVECTOR_PASSWORD_KEY=postgres-credentials::password
# - Nested JSON: SECRETS_PGVECTOR_PASSWORD_KEY=database::credentials::password
```

### Secret Key Formats

The system supports two secret key formats:

#### 1. Simple Secret (Entire Value)
```bash
SECRETS_PGVECTOR_PASSWORD_KEY=postgres-password
```
Retrieves the entire secret value from Google Cloud Secret Manager.

#### 2. JSON Field Extraction (Recommended)
```bash
# Extract 'password' field from JSON secret
SECRETS_PGVECTOR_PASSWORD_KEY=postgres-credentials::password

# Extract nested field from JSON secret
SECRETS_PGVECTOR_PASSWORD_KEY=database::credentials::password

# Complex secret names (as in your example)
SECRETS_PGVECTOR_PASSWORD_KEY=gcp_cloudsql-otp-bs-gcp-csai-n-dev-psql-001-credentials-1::password
```

For JSON field extraction, the secret in Google Cloud Secret Manager should contain JSON data like:
```json
{
  "password": "your-actual-password",
  "username": "postgres",
  "host": "127.0.0.1",
  "port": 5432
}
```

### Secret Types

The application supports the following managed secrets:

| Secret Type | Env Variable | Secret Key Setting | When Used |
|-------------|--------------|-------------------|-----------|
| PostgreSQL Database Password | `POSTGRES_PASSWORD` | `SECRETS_PGVECTOR_PASSWORD_KEY` | Always (required for database connection) |
| Message Broker Password | `BROKER_PASSWORD` | `SECRETS_BROKER_PASSWORD_KEY` | When constructing broker connection (RabbitMQ, Redis, Pub/Sub) |
| OpenAI API Key | `OPENAI_API_KEY` | `SECRETS_OPENAI_API_KEY` | When `LLM_PROVIDER=openai` |
| Anthropic API Key | `ANTHROPIC_API_KEY` | `SECRETS_ANTHROPIC_API_KEY` | When `LLM_PROVIDER=anthropic` |
| Mistral API Key | `MISTRAL_API_KEY` | `SECRETS_MISTRAL_API_KEY` | When `LLM_PROVIDER=mistral` |
| AWS Bedrock Access Key | `AWS_BEDROCK_ACCESS_KEY` | `SECRETS_AWS_BEDROCK_ACCESS_KEY` | When `LLM_PROVIDER=aws_bedrock` |
| AWS Bedrock Secret Key | `AWS_BEDROCK_SECRET_KEY` | `SECRETS_AWS_BEDROCK_SECRET_KEY` | When `LLM_PROVIDER=aws_bedrock` |

**All secret keys are optional.** If a secret key is not configured, the application falls back to the corresponding environment variable. This allows for flexible deployment scenarios where some credentials come from secret managers and others from environment variables.

### Google Cloud Secret Manager Setup

#### For Simple Secrets

1. **Create a simple secret** in Google Cloud Secret Manager:
   ```bash
   # Simple password secret
   echo -n "your-postgres-password" | gcloud secrets create postgres-password --data-file=-
   ```

#### For JSON Secrets (Recommended)

1. **Create a JSON secret** in Google Cloud Secret Manager:
   ```bash
   # Create JSON credentials secret
   cat << EOF | gcloud secrets create postgres-credentials --data-file=-
   {
     "password": "your-postgres-password",
     "username": "postgres", 
     "host": "127.0.0.1",
     "port": 5432
   }
   EOF
   ```

   For complex secret names (like in your example):
   ```bash
   cat << EOF | gcloud secrets create gcp_cloudsql-otp-bs-gcp-csai-n-dev-psql-001-credentials-1 --data-file=-
   {
     "password": "your-postgres-password",
     "username": "postgres",
     "host": "127.0.0.1",
     "port": 5432
   }
   EOF
   ```

#### Creating Secrets for Broker and Provider Credentials

1. **Message Broker Credentials** (RabbitMQ, Redis, Pub/Sub):
   ```bash
   cat << EOF | gcloud secrets create broker-credentials --data-file=-
   {
     "password": "your-broker-password",
     "username": "guest",
     "host": "rabbitmq.example.com",
     "port": 5672
   }
   EOF
   ```

2. **LLM Provider API Keys**:
   ```bash
   # OpenAI
   echo -n "sk-..." | gcloud secrets create openai-api-key --data-file=-
   
   # Anthropic
   echo -n "sk-ant-..." | gcloud secrets create anthropic-api-key --data-file=-
   
   # Mistral
   echo -n "your-mistral-key" | gcloud secrets create mistral-api-key --data-file=-
   
   # AWS Bedrock (both keys in one JSON secret)
   cat << EOF | gcloud secrets create aws-bedrock-credentials --data-file=-
   {
     "access_key": "AKIA...",
     "secret_key": "your-secret-key"
   }
   EOF
   ```

3. **Grant access** to your service account for all secrets:
   ```bash
   for secret in broker-credentials openai-api-key anthropic-api-key mistral-api-key aws-bedrock-credentials; do
     gcloud secrets add-iam-policy-binding $secret \
       --member="serviceAccount:your-service-account@project.iam.gserviceaccount.com" \
       --role="roles/secretmanager.secretAccessor"
   done
   ```

3. **Set up credentials** - ensure your service account key is available and `GOOGLE_APPLICATION_CREDENTIALS` points to it.

## Usage Examples

### Basic Configuration

```python
from aviator.settings import Settings

# Environment variable approach (default)
settings = Settings(
    secrets_manager="environment",
    postgres_password="my-password"
)

# Google Cloud Secret Manager - Database password only
settings = Settings(
    secrets_manager="google",
    secrets_pgvector_password_key="postgres-password",
    google_cloud_project="my-project"
)

# Google Cloud Secret Manager - Multiple credentials
settings = Settings(
    secrets_manager="google",
    secrets_pgvector_password_key="postgres-password",
    secrets_broker_password_key="broker-credentials::password",
    secrets_openai_api_key="openai-credentials::api_key",
    google_cloud_project="my-project"
)

# Google Cloud Secret Manager - Complex secret names with JSON field extraction
settings = Settings(
    secrets_manager="google",
    secrets_pgvector_password_key="gcp_cloudsql-otp-bs-gcp-csai-n-dev-psql-001-credentials-1::password",
    secrets_broker_password_key="rabbitmq-credentials::password",
    secrets_openai_api_key="llm-keys::openai::api_key",
    google_cloud_project="my-project"
)
```

### Docker Compose Example

```yaml
version: '3.8'
services:
  aviator:
    image: aviator-adt:latest
    environment:
      - SECRETS_MANAGER=google
      - SECRETS_PGVECTOR_PASSWORD_KEY=postgres-password
      - GOOGLE_CLOUD_PROJECT=my-project-id
      - GOOGLE_APPLICATION_CREDENTIALS=/app/service-account-key.json
    volumes:
      - ./service-account-key.json:/app/service-account-key.json:ro
```

### Kubernetes Example

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: aviator-deployment
spec:
  template:
    spec:
      containers:
      - name: aviator
        image: aviator-adt:latest
        env:
        - name: SECRETS_MANAGER
          value: "google"
        - name: SECRETS_PGVECTOR_PASSWORD_KEY
          value: "gcp_cloudsql-otp-bs-gcp-csai-n-dev-psql-001-credentials-1::password"
        - name: GOOGLE_CLOUD_PROJECT
          value: "my-project-id"
        - name: GOOGLE_APPLICATION_CREDENTIALS
          value: "/var/secrets/google/key.json"
        volumeMounts:
        - name: google-service-account
          mountPath: "/var/secrets/google"
          readOnly: true
      volumes:
      - name: google-service-account
        secret:
          secretName: google-service-account-key
```

## Error Handling and Fallbacks

The system implements robust error handling with fallback mechanisms:

1. **Missing Configuration**: If `SECRETS_MANAGER=google` but a secret key is not set, falls back to the corresponding environment variable (e.g., `POSTGRES_PASSWORD`, `BROKER_PASSWORD`, `OPENAI_API_KEY`, etc.).

2. **Secret Not Found**: If the specified secret doesn't exist in Google Cloud Secret Manager, falls back to the corresponding environment variable.

3. **Authentication Errors**: If Google Cloud authentication fails, falls back to environment variables.

4. **Network Issues**: If there are network connectivity issues with Google Cloud APIs, falls back to environment variables.

All fallback scenarios are logged with appropriate warning or error messages.

## Security Best Practices

1. **Service Account Permissions**: Grant minimal required permissions (`roles/secretmanager.secretAccessor`) to your service account.

2. **Secret Rotation**: Use Google Cloud Secret Manager's versioning feature for secret rotation:
   ```bash
   echo -n "new-password" | gcloud secrets versions add postgres-password --data-file=-
   ```

3. **Access Logging**: Enable Cloud Audit Logs for Secret Manager to track secret access.

4. **Environment Separation**: Use different secrets for different environments (dev/staging/prod).

## Monitoring and Troubleshooting

### Logging

The secrets manager integration provides detailed logging:

```python
import logging
logging.getLogger("aviator.services.secrets").setLevel(logging.INFO)
```

Key log messages:
- ✅ `Successfully retrieved postgres password from Google Cloud Secret Manager`
- ⚠️ `SECRETS_MANAGER is set to 'google' but SECRETS_PGVECTOR_PASSWORD_KEY is not configured`
- ❌ `Failed to retrieve postgres password from Google Cloud Secret Manager`

### Health Checks

You can verify the secrets integration is working by checking the application logs during startup. The postgres connection initialization will log success or failure.

### Common Issues

1. **Authentication Errors**:
   - Verify `GOOGLE_APPLICATION_CREDENTIALS` path is correct
   - Ensure service account has proper permissions
   - Check that the service account key hasn't expired

2. **Secret Not Found**:
   - Verify the secret exists: `gcloud secrets describe postgres-password`
   - Check the secret name matches `SECRETS_PGVECTOR_PASSWORD_KEY`
   - Ensure you're using the correct project

3. **Network Connectivity**:
   - Verify outbound HTTPS access to Google Cloud APIs
   - Check firewall rules and proxy configurations

## Testing

The secrets manager integration includes comprehensive test coverage. Run tests with:

```bash
# Run all tests
uv run pytest

# Run secrets-specific tests
uv run pytest tests/services/test_secrets.py
uv run pytest tests/test_settings_secrets.py
```

## API Reference

### Secret Key Format

The secret key format supports two patterns:

1. **Simple Secret**: `secret-name`
   - Returns the entire secret value
   
2. **JSON Field Extraction**: `secret-name::field::path`
   - `secret-name` - The actual secret name in Google Cloud Secret Manager
   - `field::path` - Dot-notation path to extract from JSON payload
   - Supports nested fields like `database::credentials::password`

### SecretsManager Class

```python
from aviator.services.secrets import SecretsManager, create_secrets_manager

# Create secrets manager
manager = create_secrets_manager("google", project_id="my-project")

# Get simple secret
secret_value = manager.get_secret("my-secret")

# Get JSON field
password = manager.get_secret("credentials::password")

# Get nested JSON field
nested_value = manager.get_secret("config::database::credentials::password")
```

### Settings Integration

The `Settings` class automatically handles secret retrieval based on configuration:

```python
from aviator.settings import Settings

settings = Settings(
    secrets_manager="google",
    secrets_pgvector_password_key="postgres-password"
)

# The postgres_connection will automatically use the secret from Google Cloud
connection = settings.postgres_connection
```