# AWS MCP Server Integration for Holmes

This directory contains resources for deploying the AWS API MCP (Model Context Protocol) server for Holmes, enabling comprehensive AWS service queries including CloudWatch Container Insights for investigating Kubernetes issues.

## Overview

The AWS MCP server provides Holmes with direct access to AWS APIs through a secure, read-only interface. The server is packaged as a Docker container using Supergateway to expose the stdio-based AWS MCP as an SSE (Server-Sent Events) API, making it accessible as a remote MCP server within Kubernetes.

## Architecture

```
Holmes → Remote MCP (SSE API) → Supergateway Wrapper → AWS MCP Server → AWS APIs
                                        ↓
                          Running in Kubernetes with IRSA
                          (IAM Roles for Service Accounts)
```

## Resource Files in This Directory

### Core Files

- **`Dockerfile`** - Wraps the stdio-based AWS MCP server with Supergateway to expose it as an SSE API service
  - Base image: `supercorp/supergateway:latest` (provides SSE API wrapper)
  - Installs Python and the `awslabs.aws-api-mcp-server` package
  - Exposes port 8000 for remote MCP connections
  - Converts stdio interface to HTTP SSE API for remote access

- **`aws-mcp-iam-policy.json`** - Comprehensive IAM policy with read-only permissions for AWS services
  - Covers: CloudWatch, EC2, EKS, ECS, RDS, S3, IAM, Cost Management, and more
  - All permissions are read-only (Get*, List*, Describe*)
  - Can be shared across multiple EKS clusters
  - No destructive operations allowed

- **`setup-multi-account-iam.sh`** - Sets up cross-account OIDC and IAM roles for multiple AWS accounts
  - Configures `assume_role_with_web_identity` across multiple target accounts
  - Creates OIDC providers in target accounts for each source cluster
  - Creates IAM roles in target accounts that can be assumed from any configured cluster
  - Usage: `./scripts/setup-multi-account-iam.sh setup [config-file] [permissions-file]`
  - Requires a YAML config file defining clusters and target accounts (see `multi-cluster-config-example.yaml`)

- **`enable-oidc-provider.sh`** - Enables OIDC provider for EKS cluster (prerequisite for IRSA)


### Multi-Account Setup with setup-multi-account-iam.sh

For scenarios where you need to access multiple AWS accounts from your EKS clusters, use `setup-multi-account-iam.sh`. This script sets up cross-account OIDC providers and IAM roles that enable `assume_role_with_web_identity` across all your accounts.

#### When to Use Multi-Account Setup

- You have multiple AWS accounts (dev, staging, prod, etc.)
- You want pods in any cluster to access resources in target accounts
- You need centralized IAM role management across accounts
- You're using AWS Organizations or multi-account architectures

#### How It Works

The script creates:
1. **OIDC Providers** in each target account for each source cluster
2. **IAM Roles** in target accounts that can be assumed via `assume_role_with_web_identity`
3. **Trust Policies** that allow pods from any configured cluster to assume the role

This enables pods running in any of your clusters to assume roles in target accounts and access AWS resources there.

#### Configuration File

Create a YAML config file (see `multi-cluster-config-example.yaml` for reference):

```yaml
clusters:
  - name: prod-cluster
    region: us-east-1
    account_id: "1111111111"
    oidc_issuer_id: AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
    oidc_issuer_url: https://oidc.eks.us-east-1.amazonaws.com/id/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA

  - name: staging-cluster
    region: us-west-2
    account_id: "1111111111"
    oidc_issuer_id: BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB
    oidc_issuer_url: https://oidc.eks.us-west-2.amazonaws.com/id/BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB

kubernetes:
  namespace: default
  service_account: multi-account-mcp-sa

iam:
  role_name: EKSMultiAccountMCPRole
  policy_name: MCPReadOnlyPolicy
  session_duration: 3600

target_accounts:
  - profile: dev
    account_id: "1111111111"
    description: "Development account"
    
  - profile: prod
    account_id: "2222222222"
    description: "Production account"
```

#### Getting OIDC Issuer Information

For each cluster, you need the OIDC issuer ID and URL:

```bash
# Get OIDC issuer URL
aws eks describe-cluster --name <cluster-name> --query "cluster.identity.oidc.issuer" --output text

# Extract issuer ID from the URL
# URL format: https://oidc.eks.<region>.amazonaws.com/id/<ISSUER_ID>
```

#### Running the Setup

```bash
# Basic usage (uses default config: multi-cluster-config.yaml)
./scripts/setup-multi-account-iam.sh setup

# With custom config file
./scripts/setup-multi-account-iam.sh setup my-config.yaml

# With custom permissions file
./scripts/setup-multi-account-iam.sh setup my-config.yaml ./aws-mcp-iam-policy.json

# Verify the setup
./scripts/setup-multi-account-iam.sh verify my-config.yaml

# Teardown (removes all created resources)
./scripts/setup-multi-account-iam.sh teardown my-config.yaml
```

#### What the Script Does

For each target account:
1. **Creates OIDC Providers**: Sets up OIDC providers for each cluster in the target account
2. **Creates IAM Role**: Creates a role with trust policy allowing `assume_role_with_web_identity` from all configured clusters
3. **Attaches Permissions**: Applies the read-only permissions policy to the role

#### Prerequisites

- AWS CLI configured with profiles for each target account
- `jq` and `yq` installed (`brew install jq yq` or `apt-get install jq yq`)
- Permissions to create IAM roles and OIDC providers in target accounts
- OIDC issuer information for each source cluster

#### Example: Accessing Multiple Accounts

After setup, pods in any cluster can assume the role in target accounts:

```bash
# In a pod, assume role in target account
aws sts assume-role-with-web-identity \
  --role-arn arn:aws:iam::2222222222:role/EKSMultiAccountMCPRole \
  --role-session-name pod-session \
  --web-identity-token file:///var/run/secrets/eks.amazonaws.com/serviceaccount/token
```

The AWS SDK will automatically handle this when configured with the correct role ARN.

### 4. Docker Image - SSE API Wrapper

The AWS MCP server is originally a stdio-based tool. To make it accessible as a remote MCP server in Kubernetes, we wrap it with Supergateway, which converts stdio communication to an SSE (Server-Sent Events) API.

**Pre-built image available at:**
```
us-central1-docker.pkg.dev/genuine-flight-317411/devel/aws-api-mcp-server:1.0.1
```

**How the Docker image works:**
1. Uses `supercorp/supergateway:latest` as base (provides SSE API wrapper)
2. Installs Python and the AWS MCP server package
3. Supergateway exposes the stdio interface as HTTP SSE on port 8000
4. This allows Holmes to connect to it as a remote MCP server

**To build your own:**
```bash
docker build -t your-registry/aws-api-mcp-server:latest .
docker push your-registry/aws-api-mcp-server:latest
```

### 5. Verify the Setup

#### Test IRSA Configuration
```bash
# Verify service account has correct annotation
kubectl get sa aws-api-mcp-sa -n default -o yaml

# Test AWS access with a temporary pod
kubectl run aws-cli-test \
  --image=amazon/aws-cli \
  --rm -it --restart=Never \
  --overrides='{"spec":{"serviceAccountName":"aws-api-mcp-sa"}}' \
  -n default \
  -- sts get-caller-identity

# Should return the IAM role ARN, not the node's role
```

### What Information is Available

Container Insights captures:
- **OOM Events**: Exact timestamp when pod was killed
- **Exit Codes**: 137 indicates SIGKILL (often OOM)
- **Memory Metrics**: Memory usage leading up to OOM
- **Container State**: Last state before termination
- **Restart Count**: Number of times pod has restarted
- **Resource Limits**: Configured memory limits
- **Memory Working Set**: Actual memory usage over time


## Troubleshooting

### MCP Server Not Responding

1. Check pod status:
   ```bash
   kubectl get pods -l app=aws-api-mcp-server
   kubectl logs -l app=aws-api-mcp-server
   ```

## Security Considerations

- The MCP server has **read-only** access to AWS services
- Pods use temporary credentials
- No AWS credentials are stored in the cluster
- Access is scoped to specific service account

## Next Steps

1. Create evaluation tests for AWS scenarios:
   - ELB failure analysis
   - EC2 network issues
   - RDS performance problems
   - IAM permission debugging
   - Cost analysis queries

2. Enhance Holmes toolsets to leverage AWS data

3. Add more AWS service integrations as needed
