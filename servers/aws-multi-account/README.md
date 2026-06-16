# AWS Multi-Account MCP Server Integration for Holmes

This directory contains resources for deploying the AWS API MCP (Model Context Protocol) server for Holmes with multi-account support, enabling comprehensive AWS service queries including CloudWatch Container Insights for investigating Kubernetes issues.

## Overview

The AWS MCP server provides Holmes with direct access to AWS APIs through a secure, read-only interface. The server is packaged as a Docker container using Supergateway to expose the stdio-based AWS MCP as an SSE (Server-Sent Events) API, making it accessible as a remote MCP server within Kubernetes.

## Architecture

```
Holmes → Remote MCP (SSE API) → Supergateway Wrapper → AWS MCP Server → AWS APIs
                                        ↓
                          Running in Kubernetes
                          Auth: IRSA, Static Credentials, or Per-Profile Keys
```

## Authentication Modes

The multi-account server supports three authentication modes. The mode is auto-detected from the config file, or can be set explicitly with the `auth_mode` field.

### 1. IRSA (Recommended for EKS)

Uses IAM Roles for Service Accounts to assume roles in target accounts via `assume_role_with_web_identity`. No static credentials are stored — pods get temporary credentials automatically.

**Best for**: EKS clusters with OIDC provider configured.

```yaml
# auth_mode: irsa  (default, can be omitted)
region: us-east-2
profiles:
  dev:
    account_id: "111111111111"
    role_arn: "arn:aws:iam::111111111111:role/MCPReadOnlyRole"
  prod:
    account_id: "222222222222"
    role_arn: "arn:aws:iam::222222222222:role/MCPReadOnlyRole"
```

### 2. Static Credentials with AssumeRole

Uses a single set of AWS access keys (from a central/management account) to `sts:AssumeRole` into each target account. Temporary credentials are obtained for each profile and refreshed automatically.

**Best for**: Users who cannot use IRSA but have a central account that can assume roles in target accounts.

```yaml
auth_mode: static
region: us-east-2

credentials:
  access_key_id: AKIAIOSFODNN7EXAMPLE
  secret_access_key: wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY

profiles:
  dev:
    account_id: "111111111111"
    role_arn: "arn:aws:iam::111111111111:role/MCPReadOnlyRole"
  prod:
    account_id: "222222222222"
    role_arn: "arn:aws:iam::222222222222:role/MCPReadOnlyRole"
    region: us-west-2  # optional per-profile region override
```

The central credentials only need `sts:AssumeRole` permission — they don't need direct access to any AWS services. The target account roles must trust the central account.

Credentials can also be provided via environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`) instead of the config file. If both are provided, the config file values take precedence.

### 3. Static Credentials Per Profile

Uses separate AWS access keys for each profile directly — no role assumption needed.

**Best for**: Users with separate IAM users in each account and no cross-account role setup.

```yaml
auth_mode: static_per_profile
region: us-east-2

profiles:
  dev:
    account_id: "111111111111"
    access_key_id: AKIAIOSFODNN7EXAMPLE
    secret_access_key: wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
    region: us-east-1
  prod:
    account_id: "222222222222"
    access_key_id: AKIAI44QH8DHBEXAMPLE
    secret_access_key: je7MtGbClwBF/2Zp9Utk/h3yCo8nvbEXAMPLEKEY
    region: us-west-2
```

Note: This mode uses permanent credentials with no automatic refresh. Make sure to rotate keys regularly.

## Auth Mode Detection

If `auth_mode` is not explicitly set in the config, it is auto-detected:

| Config pattern | Detected mode |
|---|---|
| `credentials` section present at top level | `static` |
| Any profile has `access_key_id` | `static_per_profile` |
| Neither of the above | `irsa` (default) |

## Resource Files in This Directory

### Core Files

- **`Dockerfile`** - Wraps the stdio-based AWS MCP server with Supergateway to expose it as an SSE API service
  - Base image: `supercorp/supergateway:latest` (provides SSE API wrapper)
  - Installs Python and the `awslabs.aws-api-mcp-server` package
  - Exposes port 8000 for remote MCP connections
  - Converts stdio interface to HTTP SSE API for remote access

- **`aws_auth.py`** - Handles multi-account credential management
  - Supports IRSA, static credentials with AssumeRole, and per-profile static credentials
  - Writes `~/.aws/credentials` and `~/.aws/config` for the AWS SDK
  - Runs a background refresh thread for modes that use temporary credentials

- **`wrapper.py`** - Entry point that sets up credentials and launches the MCP server

- **`scripts/aws-mcp-iam-policy.json`** - Comprehensive IAM policy with read-only permissions for AWS services
  - Covers: CloudWatch, EC2, EKS, ECS, RDS, S3, IAM, Cost Management, and more
  - All permissions are read-only (Get*, List*, Describe*)
  - Can be shared across multiple EKS clusters
  - No destructive operations allowed

- **`scripts/setup-multi-account-iam.sh`** - Sets up cross-account OIDC and IAM roles for IRSA mode
  - Configures `assume_role_with_web_identity` across multiple target accounts
  - Creates OIDC providers in target accounts for each source cluster
  - Creates IAM roles in target accounts that can be assumed from any configured cluster
  - Usage: `./scripts/setup-multi-account-iam.sh setup [config-file] [permissions-file]`

## Deployment

### Kubernetes Secret for Static Credentials

For `static` or `static_per_profile` modes, store credentials in a Kubernetes Secret:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: aws-mcp-accounts
type: Opaque
stringData:
  accounts.yaml: |
    auth_mode: static
    region: us-east-2
    credentials:
      access_key_id: AKIAIOSFODNN7EXAMPLE
      secret_access_key: wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
    profiles:
      dev:
        account_id: "111111111111"
        role_arn: "arn:aws:iam::111111111111:role/MCPReadOnlyRole"
      prod:
        account_id: "222222222222"
        role_arn: "arn:aws:iam::222222222222:role/MCPReadOnlyRole"
```

Mount it into the pod:

```yaml
volumes:
  - name: aws-accounts
    secret:
      secretName: aws-mcp-accounts
containers:
  - name: aws-mcp
    volumeMounts:
      - name: aws-accounts
        mountPath: /etc/aws
        readOnly: true
```

### IAM Setup for Static Credentials with AssumeRole

For `static` mode, the central IAM user needs this policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "sts:AssumeRole",
      "Resource": [
        "arn:aws:iam::111111111111:role/MCPReadOnlyRole",
        "arn:aws:iam::222222222222:role/MCPReadOnlyRole"
      ]
    }
  ]
}
```

Each target account role needs a trust policy allowing the central account:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::999999999999:user/mcp-service-user"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

### IRSA Setup

For IRSA mode, see the [multi-cluster config example](scripts/multi-cluster-config-example.yaml) and use the setup script:

```bash
./scripts/setup-multi-account-iam.sh setup [config-file] [permissions-file]
```

### Docker Image

**Pre-built image available at:**
```
us-central1-docker.pkg.dev/genuine-flight-317411/mcp/aws-api-mcp-server:1.0.1
```

**To build your own:**
```bash
docker build -t your-registry/aws-api-mcp-server:latest .
docker push your-registry/aws-api-mcp-server:latest
```

## Troubleshooting

### MCP Server Not Responding

1. Check pod status:
   ```bash
   kubectl get pods -l app=aws-api-mcp-server
   kubectl logs -l app=aws-api-mcp-server
   ```

### Credential Issues

1. Check the auth mode detected in logs:
   ```
   INFO: Detected auth mode: static
   ```

2. For `static` mode, verify the central credentials can assume roles:
   ```bash
   aws sts assume-role \
     --role-arn arn:aws:iam::111111111111:role/MCPReadOnlyRole \
     --role-session-name test
   ```

3. For `irsa` mode, verify the IRSA token exists:
   ```bash
   kubectl exec -it <pod> -- cat /var/run/secrets/eks.amazonaws.com/serviceaccount/token
   ```

## Security Considerations

- The MCP server has **read-only** access to AWS services
- For IRSA and static+AssumeRole modes, pods use temporary credentials that are refreshed automatically
- For static_per_profile mode, permanent credentials are used — ensure regular key rotation
- Access is scoped per profile to specific accounts and roles
- Store credentials in Kubernetes Secrets, not in plain ConfigMaps
