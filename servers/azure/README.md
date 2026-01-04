# Azure API MCP Server for Holmes

This directory contains resources for deploying the Azure API MCP (Model Context Protocol) server for Holmes, enabling comprehensive Azure service queries for investigating Azure infrastructure, applications, and cost-related issues.

## Overview

The Azure API MCP server provides Holmes with direct access to Azure services through a secure, read-only interface. Unlike the AWS MCP which requires Supergateway, the Azure MCP natively supports streamable-http transport, making it simpler to deploy and lighter on resources.

**Setup Script Features**:
- **Idempotent**: Safe to run multiple times - checks for existing resources
- **Multi-subscription**: Creates custom role with all subscriptions in scope
- **Automatic**: Creates managed identity, role, assignments, and K8s service account
- **Comprehensive**: Includes network troubleshooting and SQL diagnostic permissions

## Features

- **Native HTTP Support**: Built-in streamable-http transport (no Supergateway needed)
- **Comprehensive Azure Access**: Query VMs, AKS, Storage, Databases, Networking, and more
- **Multi-Subscription Support**: Query across multiple subscriptions within a single tenant
- **Secure by Default**: Read-only mode with Azure RBAC enforcement
- **Multiple Auth Methods**: Supports Managed Identity, Service Principal, and Workload Identity

## Quick Start

### 1. Setup Azure Authentication

Choose one of the following authentication methods:

#### Option A: Workload Identity (Recommended for AKS)
```bash
# For all accessible subscriptions (recommended)
./setup-azure-identity.sh \
  --auth-method workload-identity \
  --resource-group YOUR_RG \
  --aks-cluster YOUR_AKS_CLUSTER \
  --all-subscriptions

# For specific subscriptions
./setup-azure-identity.sh \
  --auth-method workload-identity \
  --resource-group YOUR_RG \
  --aks-cluster YOUR_AKS_CLUSTER \
  --subscriptions "sub1,sub2,sub3"
```

**Note**: The resource group must exist in one of the specified subscriptions (usually where your AKS cluster is located).

#### Option B: Service Principal (for non-AKS environments)
```bash
# For all accessible subscriptions
./setup-azure-identity.sh \
  --auth-method service-principal \
  --all-subscriptions

# For specific subscription
./setup-azure-identity.sh \
  --auth-method service-principal \
  --subscriptions "YOUR_SUBSCRIPTION_ID"
```

#### Option C: Managed Identity (for Azure VMs/VMSS)
```bash
./setup-azure-identity.sh \
  --auth-method managed-identity
```

### 2. Build and Push Docker Image

```bash
# For Google Container Registry
./build-and-push.sh --registry gcr.io/YOUR_PROJECT

# For Azure Container Registry
./build-and-push.sh --registry YOUR_ACR.azurecr.io

# For Docker Hub
./build-and-push.sh --registry docker.io/YOUR_USERNAME
```

### 3. Deploy with Holmes

The setup script outputs the exact values you need. Update your Holmes values.yaml with the output from the script:

```yaml
mcpAddons:
  azure:
    enabled: true
    registry: "YOUR_REGISTRY"
    image: "azure-api-mcp-server:latest"
    serviceAccount:
      # Use the service account created by the setup script
      create: false
      name: "azure-api-mcp-sa"
    config:
      # These values are provided by the setup script output
      tenantId: "YOUR_TENANT_ID"
      subscriptionId: "YOUR_SUBSCRIPTION_ID"  # Primary subscription
      readOnlyMode: "true"
      authMethod: "workload-identity"  # or managed-identity, service-principal
```

**Important**: The setup script automatically creates the Kubernetes service account `azure-api-mcp-sa` with all necessary annotations for workload identity. Do not create a new one.

Deploy Holmes:
```bash
helm upgrade holmes holmes/holmes --values your-values.yaml
```

### 4. Configure Holmes MCP Client

Add to your Holmes configuration:

```yaml
mcp_servers:
  azure_api:
    description: "Azure API MCP Server - comprehensive Azure service access"
    config:
      url: "http://holmes-azure-mcp-server:8000/mcp/messages"
      mode: streamable-http
```

## Architecture

```
Holmes → Azure MCP (HTTP) → Azure API MCP Server → Azure APIs
              ↓
    Native streamable-http transport
    (No Supergateway needed)
```

## Authentication Methods

### Workload Identity (Recommended for AKS)
- Most secure option for AKS deployments
- No secrets to manage
- Automatic token rotation
- Federation between K8s service account and Azure AD
- Requires: AKS cluster with OIDC issuer enabled

### Service Principal (For non-AKS environments)
- Traditional approach using client ID + secret
- Works in any Kubernetes environment
- Good for: Non-Azure K8s clusters, Docker, local testing
- Requires managing and rotating client secret

### Managed Identity (For Azure VMs/VMSS)
- For when MCP server runs directly on Azure compute
- No credentials to manage
- Automatic token handling
- System or user-assigned identity
- Only works on Azure VMs, VMSS, or Container Instances

## Required Azure Permissions

The setup script automatically creates and assigns a comprehensive custom role "Azure MCP Reader" that includes:
- All standard read permissions (`*/read`)
- Network troubleshooting actions (effective NSGs, routing, backend health)
- SQL performance diagnostics (query store, advisors)
- Log Analytics query execution
- Storage account key listing (for connectivity testing)
- Cost management queries
- Security assessments
- Metrics and monitoring data

**How it works**:
1. The script creates ONE custom role "Azure MCP Reader" with all specified subscriptions in its AssignableScopes
2. This role is defined in `azure-rbac-roles.json`
3. The role is then assigned to the managed identity in each subscription
4. This ensures the identity can query resources across all configured subscriptions

## Testing the MCP Server

### Local Testing
```bash
# Build and run locally
docker build -t azure-mcp-local .
docker run -p 8000:8000 azure-mcp-local

# Test with curl
curl -X POST http://localhost:8000/mcp/messages \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'
```

### In-Cluster Testing
```bash
# Port-forward to test
kubectl port-forward svc/holmes-azure-mcp-server 8000:8000

# Test from Holmes
holmes ask "List all my Azure VMs in the current subscription"
holmes ask "Check the status of my AKS clusters"
holmes ask "Analyze my Azure costs for the last month"
```

## Available Azure Services

The MCP server provides access to:

- **Compute**: VMs, VMSS, Availability Sets
- **Containers**: AKS, Container Instances, Container Registry
- **Storage**: Storage Accounts, Blobs, Files, Queues, Tables
- **Networking**: VNets, Subnets, NSGs, Load Balancers, Application Gateways
- **Databases**: SQL Database, Cosmos DB, PostgreSQL, MySQL
- **App Services**: Web Apps, Function Apps, Logic Apps
- **Monitoring**: Metrics, Logs, Alerts, Application Insights
- **Cost Management**: Cost analysis, Budgets, Recommendations
- **Security**: Security Center, Key Vault (metadata only)

## Troubleshooting

### MCP Server Not Starting
```bash
# Check logs
kubectl logs deployment/holmes-azure-mcp-server

# Verify authentication
kubectl describe pod -l app=holmes-azure-mcp-server
```

### Authentication Issues
```bash
# Verify service account annotations
kubectl get sa azure-api-mcp-sa -o yaml

# Check federated credentials
az identity federated-credential list \
  --resource-group YOUR_RG \
  --identity-name azure-mcp-identity
```

### Connection Issues
```bash
# Test connectivity
kubectl exec -it deployment/holmes -- curl http://holmes-azure-mcp-server:8000/health

# Check network policies
kubectl get networkpolicy
```

## Security Considerations

1. **Custom Role with Comprehensive Permissions**: Uses "Azure MCP Reader" role for investigation and troubleshooting
2. **Read-Only Operations**: No write, delete, or modify permissions included
3. **RBAC Enforcement**: Azure RBAC is always enforced
4. **No Credential Storage**: Use workload identity for AKS when possible
5. **Network Isolation**: Use network policies to restrict access
6. **Audit Logging**: All API calls are logged in Azure Activity Log

## Multi-Subscription Access

The setup script can configure access to multiple subscriptions:

```bash
# Configure for specific subscriptions
./setup-azure-identity.sh \
  --auth-method workload-identity \
  --resource-group YOUR_RG \
  --aks-cluster YOUR_AKS \
  --subscriptions "sub1,sub2,sub3"

# Configure for all accessible subscriptions
./setup-azure-identity.sh \
  --auth-method service-principal \
  --all-subscriptions
```

The MCP server can then query resources across all configured subscriptions within the same tenant.

## Differences from AWS MCP

| Feature | Azure MCP | AWS MCP |
|---------|-----------|---------|
| Transport | Native streamable-http | Requires Supergateway wrapper |
| Language | Go | Python |
| Size | ~20MB | ~200MB |
| Startup Time | <5 seconds | ~15 seconds |
| Memory Usage | ~50MB | ~200MB |

## Support

For issues or questions:
1. Check the [Azure MCP repository](https://github.com/Azure/azure-api-mcp)
2. Review Holmes documentation
3. Open an issue in the Holmes repository