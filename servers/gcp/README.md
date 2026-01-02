# GCP MCP Integration for Holmes

This directory contains the Google Cloud Platform (GCP) MCP servers for Holmes, enabling AI-powered investigation and troubleshooting of GCP resources.

## Overview

The GCP MCP addon provides three specialized servers:

- **gcloud MCP** - General GCP management via gcloud CLI commands. Supports querying resources across multiple GCP projects.
- **Observability MCP** - Cloud Logging, Monitoring, Trace, and Error Reporting. Can retrieve historical logs for deleted Kubernetes resources.
- **Storage MCP** - Cloud Storage operations and management.

## Prerequisites

- `gcloud` CLI installed and authenticated
- `kubectl` configured with access to your Kubernetes cluster
- Holmes installed or ready to install via Helm
- GCP permissions to create service accounts and grant IAM roles

## Quick Start

### Step 1: Create Service Account

Run the automated setup script to create a GCP service account with appropriate permissions:

```bash
# Single project setup
./setup-gcp-service-account.sh --project my-project --k8s-namespace holmes

# Multi-project setup
./setup-gcp-service-account.sh \
  --project primary-project \
  --other-projects dev-project,staging-project,prod-project \
  --k8s-namespace holmes
```

The script will:
- Create a GCP service account
- Grant ~50 optimized read-only IAM roles for incident response
- Generate a service account key
- Create a Kubernetes secret (`gcp-sa-key`)

### Step 2: Configure Holmes Helm Values

Add to your `values.yaml`:

```yaml
mcpAddons:
  gcp:
    enabled: true

    # Reference the secret created by setup script
    serviceAccountKey:
      secretName: "gcp-sa-key"

    # Optional: specify primary project/region
    config:
      project: "your-primary-project"  # Optional
      region: "us-central1"            # Optional

    # Enable the MCP servers you need
    gcloud:
      enabled: true
    observability:
      enabled: true
    storage:
      enabled: true
```

### Step 3: Deploy Holmes

```bash
helm upgrade --install holmes robusta/holmes \
  --namespace holmes \
  --create-namespace \
  --values values.yaml
```

### Step 4: Verify

```bash
# Check pods are running
kubectl get pods -n holmes | grep gcp-mcp

# Test with Holmes
holmes ask "List all GKE clusters in my GCP projects"
```

## Service Account Setup Details

### Script Options

```bash
./setup-gcp-service-account.sh [OPTIONS]

Options:
  -n, --name NAME                Service account name (default: holmes-gcp-mcp)
  -p, --project PROJECT          Primary GCP project ID (required)
  -o, --other-projects PROJECTS  Comma-separated additional projects
  -k, --key-file PATH            Key file path (default: ~/SA_NAME-key.json)
  -s, --k8s-namespace NAMESPACE  Kubernetes namespace (default: default)
  --no-k8s-secret               Skip creating Kubernetes secret
  -h, --help                     Show help message
```

### Permissions Granted

The script grants ~50 optimized read-only roles designed for incident response and troubleshooting:

**What's Included:**
- ✅ Complete audit log visibility (who changed what)
- ✅ Full networking troubleshooting (firewalls, load balancers, SSL)
- ✅ Database and BigQuery metadata (schemas, configurations)
- ✅ Security findings and IAM analysis
- ✅ Container and Kubernetes visibility
- ✅ Monitoring, logging, and tracing

**Security Boundaries:**
- ❌ NO actual data access (cannot read storage objects or BigQuery data)
- ❌ NO secret values (only metadata)
- ❌ NO write permissions

Key roles include:
- `roles/browser` - Navigate org/folder/project hierarchy
- `roles/logging.privateLogViewer` - Audit logs and data access logs
- `roles/compute.viewer` - VMs, firewalls, load balancers
- `roles/container.viewer` - GKE clusters and workloads
- `roles/monitoring.viewer` - Metrics and alerts
- `roles/iam.securityReviewer` - IAM policies
- `roles/storage.legacyBucketReader` - Bucket metadata (no object access)
- `roles/bigquery.metadataViewer` - Table schemas only

### Multi-Project Configuration

For organizations with multiple GCP projects:

```bash
./setup-gcp-service-account.sh \
  --project primary-project \
  --other-projects dev,staging,prod
```

This creates a service account with:
- **Primary project**: Full set of ~50 optimized roles
- **Other projects**: Same optimized roles across all projects

The service account can investigate resources across all specified projects.

### Manual Setup (Alternative)

If you prefer manual configuration:

1. Create service account:
```bash
gcloud iam service-accounts create holmes-gcp-mcp \
  --display-name="Holmes GCP MCP Service Account"
```

2. Grant roles (example with essential roles):
```bash
PROJECT_ID=your-project
SA_EMAIL=holmes-gcp-mcp@${PROJECT_ID}.iam.gserviceaccount.com

# Essential roles for basic functionality
for role in browser compute.viewer container.viewer logging.privateLogViewer monitoring.viewer; do
  gcloud projects add-iam-policy-binding ${PROJECT_ID} \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/${role}"
done
```

3. Create key:
```bash
gcloud iam service-accounts keys create key.json \
  --iam-account=${SA_EMAIL}
```

4. Create Kubernetes secret:
```bash
kubectl create secret generic gcp-sa-key \
  --from-file=key.json \
  --namespace=holmes
```

## Troubleshooting

### Common Issues

**Authentication Errors**
```bash
# Check if secret is mounted
kubectl exec -n holmes deployment/holmes-gcp-mcp-server -c gcloud-mcp -- \
  ls -la /var/secrets/gcp/

# Verify authentication
kubectl exec -n holmes deployment/holmes-gcp-mcp-server -c gcloud-mcp -- \
  gcloud auth list
```

**Permission Denied**
```bash
# Check IAM bindings
gcloud projects get-iam-policy PROJECT_ID \
  --flatten="bindings[].members" \
  --filter="bindings.members:holmes-gcp-mcp@"

# Solution: Check if the required role is granted
```

**Pod Not Starting**
```bash
# Check pod events
kubectl describe pod -n holmes -l app.kubernetes.io/component=gcp-mcp-server

# Check logs
kubectl logs -n holmes deployment/holmes-gcp-mcp-server --all-containers
```

**gcloud MCP Specific Issues**

The gcloud MCP requires gcloud version 550.0.0+ to avoid field name compatibility issues. The provided Docker image includes the correct version.

Note: gcloud CLI doesn't support Workload Identity token refresh, so service account keys are required for the gcloud MCP.

## Security Best Practices

1. **Least Privilege**: The script only grants read-only roles without data access
2. **Rotate Keys Regularly**: Re-run setup script every 90 days
3. **Delete Local Keys**: Remove key files after creating Kubernetes secret
4. **Monitor Usage**: Check audit logs for service account activity
5. **Enable Network Policies**: Set `networkPolicy.enabled: true` in Helm values

## Docker Images

Pre-built images are available:
- `us-central1-docker.pkg.dev/genuine-flight-317411/holmesgpt/gcloud-cli-mcp:1.2.0`
- `us-central1-docker.pkg.dev/genuine-flight-317411/holmesgpt/gcloud-observability-mcp:1.0.0`
- `us-central1-docker.pkg.dev/genuine-flight-317411/holmesgpt/gcloud-storage-mcp:1.0.0`

To build custom images:
```bash
cd gcloud && ./build_push.sh YOUR_REGISTRY/gcloud-cli-mcp:tag
cd observability && ./build_push.sh YOUR_REGISTRY/gcloud-observability-mcp:tag
cd storage && ./build_push.sh YOUR_REGISTRY/gcloud-storage-mcp:tag
```

## Support

For issues or questions:
1. Check the troubleshooting section above
2. Review Holmes logs: `kubectl logs -n holmes deployment/holmes-gcp-mcp-server`
3. Visit [Holmes documentation](https://holmesgpt.dev/)
4. File an issue at [Holmes GitHub repository](https://github.com/robusta-dev/holmesgpt)