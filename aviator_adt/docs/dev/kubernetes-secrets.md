# Kubernetes Secret Provider Testing

This guide explains how to test the Kubernetes secret provider implementation using Skaffold for local development.

## Overview

The Kubernetes secret provider enables Aviator to read secrets directly from Kubernetes secrets instead of environment variables, providing:

- **Enhanced Security**: Secrets are stored in Kubernetes with proper access controls
- **Dynamic Retrieval**: Secrets can be updated without pod restarts (subject to caching) 
- **Field Extraction**: Support for extracting specific fields from JSON secrets using `::` syntax

## Architecture

The Kubernetes secret provider works by:

1. Using the pod's service account token from `/var/run/secrets/kubernetes.io/serviceaccount/token`
2. Reading the namespace from `/var/run/secrets/kubernetes.io/serviceaccount/namespace` 
3. Making API calls to `https://kubernetes.default.svc/api/v1/namespaces/{namespace}/secrets/{secret-name}`
4. Decoding base64-encoded secret values
5. Supporting field extraction with `::` syntax (e.g., `secret-name::field`)

## Quick Start

### Prerequisites

- Kubernetes cluster (local or remote)
- `kubectl` configured to access the cluster
- `skaffold` installed
- Valid cluster context

### Deploy and Test

1. **Start backend services:**
   ```bash
   skaffold -f skaffold-backend.yaml run
   ```

2. **Deploy main application:**
   ```bash
   skaffold -f skaffold.yaml run -p kubernetes-secrets
   ```

3. **Run automated tests:**
   ```bash
   ./test-kubernetes-secrets.sh
   ```

## Configuration Details

### Environment Variables

| Variable | Value | Description |
|----------|-------|-------------|
| `SECRETS_MANAGER` | `kubernetes` | Enables Kubernetes secret provider |
| `SECRETS_PGVECTOR_PASSWORD_KEY` | `aviator-db-secret::password` | Secret reference for database password |
| `SECRETS_BROKER_PASSWORD_KEY` | `aviator-broker-secret::password` | Secret reference for message broker password |
| `SECRETS_OPENAI_API_KEY` | `aviator-openai-secret::api_key` | Secret reference for OpenAI API key |
| `SECRETS_ANTHROPIC_API_KEY` | `aviator-anthropic-secret::api_key` | Secret reference for Anthropic API key |
| `SECRETS_MISTRAL_API_KEY` | `aviator-mistral-secret::api_key` | Secret reference for Mistral API key |
| `SECRETS_AWS_BEDROCK_ACCESS_KEY` | `aviator-aws-secret::bedrock_access_key` | Secret reference for AWS Bedrock access key |
| `SECRETS_AWS_BEDROCK_SECRET_KEY` | `aviator-aws-secret::bedrock_secret_key` | Secret reference for AWS Bedrock secret key |
| `KUBERNETES_CONFIG` | `{}` (optional) | JSON configuration for API endpoint, namespace override, etc. Example: `{"endpoint": "https://kubernetes.default.svc", "timeout": 60}` |

### Secret Types

The application supports the following managed secrets via Kubernetes:

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
