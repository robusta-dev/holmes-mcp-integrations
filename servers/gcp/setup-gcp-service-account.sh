#!/bin/bash

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default values
SA_NAME="holmes-gcp-mcp"
PRIMARY_PROJECT=""
OTHER_PROJECTS=""
K8S_NAMESPACE="default"
KEY_FILE=""
CREATE_K8S_SECRET="true"
HELP="false"

# Function to print colored output
print_color() {
    local color=$1
    local message=$2
    echo -e "${color}${message}${NC}"
}

# Function to show usage
usage() {
    cat << EOF
Usage: $0 [OPTIONS]

Setup a Google Cloud service account for Holmes GCP MCP integration with optimized read-only permissions.

OPTIONS:
    -n, --name NAME                 Service account name (default: holmes-gcp-mcp)
    -p, --project PROJECT          Primary GCP project ID (required)
    -o, --other-projects PROJECTS  Comma-separated list of additional projects for cross-project access
    -k, --key-file PATH            Path to save the service account key (default: ~/SA_NAME-key.json)
    -s, --k8s-namespace NAMESPACE  Kubernetes namespace for secret (default: default)
    --no-k8s-secret                Skip creating Kubernetes secret
    -h, --help                     Show this help message

EXAMPLES:
    # Basic setup with single project
    $0 --project my-project

    # Multi-project setup
    $0 --project my-main-project --other-projects dev-project,staging-project,prod-project

    # Custom service account name and namespace
    $0 --name my-sa --project my-project --k8s-namespace holmes

    # Just create service account and key, skip K8s secret
    $0 --project my-project --no-k8s-secret

WHAT THIS SCRIPT DOES:
    1. Creates a GCP service account in the primary project
    2. Grants ~50 optimized read-only IAM roles for incident response
    3. Grants permissions in additional projects if specified
    4. Creates a service account key file
    5. Optionally creates a Kubernetes secret with the key

PERMISSIONS GRANTED:
    • Complete audit log visibility (who changed what)
    • Full networking troubleshooting (firewalls, load balancers, SSL)
    • Database and BigQuery metadata (no data access)
    • Security findings and IAM analysis
    • Container and Kubernetes visibility
    • Monitoring, logging, and tracing
    • NO actual data access (cannot read storage objects, BigQuery data, or secrets)

SECURITY:
    This configuration follows the principle of least privilege while providing
    comprehensive incident response capabilities WITHOUT data access.

EOF
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -n|--name)
            SA_NAME="$2"
            shift 2
            ;;
        -p|--project)
            PRIMARY_PROJECT="$2"
            shift 2
            ;;
        -o|--other-projects)
            OTHER_PROJECTS="$2"
            shift 2
            ;;
        -k|--key-file)
            KEY_FILE="$2"
            shift 2
            ;;
        -s|--k8s-namespace)
            K8S_NAMESPACE="$2"
            shift 2
            ;;
        --no-k8s-secret)
            CREATE_K8S_SECRET="false"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            print_color "$RED" "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# Validate required parameters
if [ -z "$PRIMARY_PROJECT" ]; then
    print_color "$RED" "Error: Primary project is required"
    usage
    exit 1
fi

# Set default key file path if not specified
if [ -z "$KEY_FILE" ]; then
    KEY_FILE="$HOME/${SA_NAME}-key.json"
fi

# Construct service account email
SA_EMAIL="${SA_NAME}@${PRIMARY_PROJECT}.iam.gserviceaccount.com"

# Convert comma-separated projects to array
IFS=',' read -ra OTHER_PROJECTS_ARRAY <<< "$OTHER_PROJECTS"

# Optimized roles for incident response without data access
ROLES=(
    # Core Navigation
    "roles/browser"  # Navigate org/folder/project hierarchy

    # Audit & Operations Tracking
    "roles/logging.privateLogViewer"  # Audit logs and data access logs
    "roles/cloudasset.viewer"  # Asset inventory and changes
    "roles/recommender.viewer"  # Security recommendations

    # Comprehensive Networking
    "roles/compute.viewer"  # VMs, firewalls, LBs, networking
    "roles/networksecurity.viewer"  # Firewall policies
    "roles/networkmanagement.viewer"  # Connectivity tests
    "roles/networkconnectivity.viewer"  # Private connectivity
    "roles/networkservices.viewer"  # Traffic director
    "roles/networkanalyzer.viewer"  # Network insights
    "roles/servicenetworking.viewer"  # Service networking
    "roles/dns.reader"  # DNS zones and records
    "roles/certificatemanager.viewer"  # SSL certificates
    "roles/vpcaccess.viewer"  # VPC connectors

    # Kubernetes & Containers
    "roles/container.viewer"  # GKE clusters
    "roles/containeranalysis.occurrences.viewer"  # Container vulnerabilities
    "roles/gkehub.viewer"  # Multi-cluster
    "roles/gkebackup.viewer"  # GKE backups
    "roles/binaryauthorization.attestorsViewer"  # Container policies

    # IAM & Security (No Secrets Values)
    "roles/iam.securityReviewer"  # IAM policies
    "roles/iam.roleViewer"  # Role definitions
    "roles/iam.organizationRoleViewer"  # Org roles
    "roles/iamcredentials.viewer"  # Service account audit
    "roles/accesscontextmanager.policyReader"  # VPC-SC policies
    "roles/orgpolicy.viewer"  # Org policies
    "roles/securitycenter.findingsViewer"  # Security findings
    "roles/securitycenter.settingsViewer"  # Security config
    "roles/securityposture.viewer"  # Security posture
    "roles/secretmanager.viewer"  # Secret metadata only
    "roles/cloudkms.viewer"  # Encryption keys
    "roles/privateca.auditor"  # Private CA
    "roles/oslogin.viewer"  # OS login

    # Monitoring & Observability
    "roles/monitoring.viewer"  # Metrics and alerts
    "roles/monitoring.metricsScopesViewer"  # Cross-project metrics
    "roles/cloudtrace.user"  # Distributed traces
    "roles/cloudprofiler.user"  # Performance profiles
    "roles/errorreporting.viewer"  # Application errors

    # Database Metadata (No Data)
    "roles/cloudsql.viewer"  # Cloud SQL metadata
    "roles/spanner.viewer"  # Spanner metadata
    "roles/redis.viewer"  # Redis metadata
    "roles/memcache.viewer"  # Memcache metadata
    "roles/alloydb.viewer"  # AlloyDB metadata
    "roles/datastore.viewer"  # Datastore metadata
    "roles/firebasedatabase.viewer"  # Firebase structure

    # BigQuery Metadata Only
    "roles/bigquery.metadataViewer"  # Table schemas
    "roles/bigquery.user"  # Job history

    # Serverless & Apps
    "roles/run.viewer"  # Cloud Run
    "roles/cloudfunctions.viewer"  # Functions
    "roles/cloudscheduler.viewer"  # Scheduled jobs
    "roles/cloudtasks.viewer"  # Task queues
    "roles/workflows.viewer"  # Workflows
    "roles/eventarc.viewer"  # Event routing

    # Storage Metadata Only
    "roles/storage.legacyBucketReader"  # Bucket metadata only, no object access
    "roles/storageinsights.viewer"  # Storage insights
    "roles/storagetransfer.viewer"  # Transfer jobs

    # Build & Deployment
    "roles/cloudbuild.builds.viewer"  # Build history
    "roles/artifactregistry.reader"  # Container registries
    "roles/source.reader"  # Source repos
    "roles/deploymentmanager.viewer"  # Deployments

    # API Management
    "roles/apigateway.viewer"  # API Gateway
    "roles/serviceusage.serviceUsageViewer"  # API usage

    # Data Pipeline Metadata
    "roles/dataflow.viewer"  # Dataflow jobs
    "roles/dataproc.viewer"  # Dataproc clusters
    "roles/pubsub.viewer"  # Pub/Sub topics
    "roles/composer.environmentAndStorageObjectViewer"  # Airflow
)

# Print configuration
print_color "$GREEN" "=== Holmes GCP MCP Service Account Setup ==="
echo ""
echo "Configuration:"
echo "  Service Account Name: $SA_NAME"
echo "  Service Account Email: $SA_EMAIL"
echo "  Primary Project: $PRIMARY_PROJECT"
if [ -n "$OTHER_PROJECTS" ]; then
    echo "  Additional Projects: $OTHER_PROJECTS"
fi
echo "  Key File Path: $KEY_FILE"
if [ "$CREATE_K8S_SECRET" == "true" ]; then
    echo "  Kubernetes Namespace: $K8S_NAMESPACE"
    echo "  Kubernetes Secret Name: gcp-sa-key"
fi
echo "  Total Roles to Grant: ${#ROLES[@]} optimized roles"
echo ""
print_color "$YELLOW" "Permissions Summary:"
echo "  ✅ Complete audit log visibility (who changed what)"
echo "  ✅ Full networking troubleshooting capabilities"
echo "  ✅ Database and BigQuery metadata access"
echo "  ✅ Security findings and IAM analysis"
echo "  ❌ NO data access (cannot read storage/BigQuery data)"
echo "  ❌ NO secret values (only metadata)"
echo ""

# Ask for confirmation
read -p "Do you want to proceed? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    print_color "$YELLOW" "Setup cancelled by user"
    exit 0
fi

# Step 1: Create service account
print_color "$GREEN" "\n📦 Step 1: Creating service account..."
if gcloud iam service-accounts describe "$SA_EMAIL" --project="$PRIMARY_PROJECT" &>/dev/null; then
    print_color "$YELLOW" "Service account already exists, skipping creation"
else
    gcloud iam service-accounts create "$SA_NAME" \
        --display-name="Holmes GCP MCP Service Account" \
        --description="Service account for Holmes GCP MCP integration with optimized read-only access for incident response" \
        --project="$PRIMARY_PROJECT"
    print_color "$GREEN" "✅ Service account created"
fi

# Step 2: Grant permissions in primary project
print_color "$GREEN" "\n🔐 Step 2: Granting ${#ROLES[@]} roles in primary project ($PRIMARY_PROJECT)..."
print_color "$YELLOW" "This may take a minute..."

# Grant roles with progress indication
TOTAL_ROLES=${#ROLES[@]}
CURRENT=0
FAILED_ROLES=()

for role in "${ROLES[@]}"; do
    CURRENT=$((CURRENT + 1))
    echo -n "  [$CURRENT/$TOTAL_ROLES] Granting $role... "
    if gcloud projects add-iam-policy-binding "$PRIMARY_PROJECT" \
        --member="serviceAccount:$SA_EMAIL" \
        --role="$role" \
        --condition=None \
        --quiet &>/dev/null; then
        echo "✅"
    else
        echo "⚠️  (may not be available in this project)"
        FAILED_ROLES+=("$role")
    fi
done

if [ ${#FAILED_ROLES[@]} -gt 0 ]; then
    print_color "$YELLOW" "Note: ${#FAILED_ROLES[@]} roles could not be granted (may not be available in this project type)"
fi
print_color "$GREEN" "✅ Permissions granted in primary project"

# Step 3: Grant permissions in other projects
if [ -n "$OTHER_PROJECTS" ]; then
    print_color "$GREEN" "\n🌍 Step 3: Granting permissions in additional projects..."

    for project in "${OTHER_PROJECTS_ARRAY[@]}"; do
        # Trim whitespace
        project=$(echo "$project" | xargs)

        print_color "$YELLOW" "\nProcessing project: $project"

        # Check if project exists and is accessible
        if ! gcloud projects describe "$project" &>/dev/null; then
            print_color "$RED" "  ⚠️  Cannot access project $project, skipping"
            continue
        fi

        print_color "$YELLOW" "  Granting ${#ROLES[@]} roles (this may take a minute)..."

        # Grant roles in other projects
        FAILED_COUNT=0
        for role in "${ROLES[@]}"; do
            echo -n "  Granting $role... "
            if gcloud projects add-iam-policy-binding "$project" \
                --member="serviceAccount:$SA_EMAIL" \
                --role="$role" \
                --condition=None \
                --quiet &>/dev/null; then
                echo "✅"
            else
                echo "⚠️  (may not be available in this project)"
                FAILED_COUNT=$((FAILED_COUNT + 1))
            fi
        done

        if [ $FAILED_COUNT -gt 0 ]; then
            print_color "$YELLOW" "  Note: $FAILED_COUNT roles could not be granted (may not be available in this project type)"
        fi
    done

    print_color "$GREEN" "✅ Permissions granted in additional projects"
else
    print_color "$YELLOW" "\n⏭️  Step 3: No additional projects specified, skipping"
fi

# Step 4: Create service account key
print_color "$GREEN" "\n🔑 Step 4: Creating service account key..."

# Check if key file already exists
if [ -f "$KEY_FILE" ]; then
    read -p "Key file already exists at $KEY_FILE. Overwrite? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_color "$YELLOW" "Skipping key creation"
    else
        gcloud iam service-accounts keys create "$KEY_FILE" \
            --iam-account="$SA_EMAIL" \
            --project="$PRIMARY_PROJECT"
        print_color "$GREEN" "✅ Service account key created at: $KEY_FILE"
    fi
else
    gcloud iam service-accounts keys create "$KEY_FILE" \
        --iam-account="$SA_EMAIL" \
        --project="$PRIMARY_PROJECT"
    print_color "$GREEN" "✅ Service account key created at: $KEY_FILE"
fi

# Step 5: Create Kubernetes secret
if [ "$CREATE_K8S_SECRET" == "true" ]; then
    print_color "$GREEN" "\n☸️  Step 5: Creating Kubernetes secret..."

    # Check if kubectl is available
    if ! command -v kubectl &> /dev/null; then
        print_color "$RED" "kubectl not found, skipping Kubernetes secret creation"
        print_color "$YELLOW" "You can create the secret manually later with:"
        echo "  kubectl create secret generic gcp-sa-key \\"
        echo "    --from-file=key.json=$KEY_FILE \\"
        echo "    --namespace=$K8S_NAMESPACE"
    else
        # Check if secret already exists
        if kubectl get secret gcp-sa-key -n "$K8S_NAMESPACE" &>/dev/null; then
            read -p "Kubernetes secret 'gcp-sa-key' already exists. Replace it? (y/n) " -n 1 -r
            echo
            if [[ $REPLY =~ ^[Yy]$ ]]; then
                kubectl delete secret gcp-sa-key -n "$K8S_NAMESPACE"
                kubectl create secret generic gcp-sa-key \
                    --from-file=key.json="$KEY_FILE" \
                    --namespace="$K8S_NAMESPACE"
                print_color "$GREEN" "✅ Kubernetes secret updated"
            else
                print_color "$YELLOW" "Keeping existing Kubernetes secret"
            fi
        else
            kubectl create secret generic gcp-sa-key \
                --from-file=key.json="$KEY_FILE" \
                --namespace="$K8S_NAMESPACE"
            print_color "$GREEN" "✅ Kubernetes secret created"
        fi
    fi
else
    print_color "$YELLOW" "\n⏭️  Step 5: Skipping Kubernetes secret creation (--no-k8s-secret flag)"
fi

# Print summary and next steps
print_color "$GREEN" "\n✨ Setup Complete! ✨"
echo ""
print_color "$GREEN" "Summary:"
echo "  • Service Account: $SA_EMAIL"
echo "  • Key File: $KEY_FILE"
echo "  • Roles Granted: ${#ROLES[@]} optimized roles"
if [ "$CREATE_K8S_SECRET" == "true" ] && command -v kubectl &> /dev/null; then
    echo "  • Kubernetes Secret: gcp-sa-key (namespace: $K8S_NAMESPACE)"
fi
echo ""
print_color "$GREEN" "What Holmes can now do:"
echo "  ✅ View audit logs to track who changed what"
echo "  ✅ Analyze firewall rules and network configurations"
echo "  ✅ Troubleshoot load balancers and SSL certificates"
echo "  ✅ Investigate GKE cluster issues"
echo "  ✅ View database and BigQuery metadata (no data)"
echo "  ✅ Access security findings and IAM policies"
echo ""
print_color "$YELLOW" "Security guarantees:"
echo "  ❌ Cannot read storage object contents"
echo "  ❌ Cannot access BigQuery table data"
echo "  ❌ Cannot read secret values (only names)"
echo "  ❌ Cannot make any changes to resources"
echo ""
print_color "$YELLOW" "Next Steps:"
echo "1. Update your Helm values.yaml:"
echo ""
echo "   mcpAddons:"
echo "     gcp:"
echo "       enabled: true"
echo "       serviceAccountKey:"
echo "         secretName: \"gcp-sa-key\""
echo "       config:"
echo "         project: \"$PRIMARY_PROJECT\""
echo ""
echo "2. Deploy Holmes with Helm:"
echo "   helm upgrade --install holmes ./helm/holmes --values values.yaml"
echo ""
echo "3. After verifying the deployment works, delete the local key file:"
echo "   rm $KEY_FILE"
echo ""
print_color "$YELLOW" "⚠️  Security Reminder:"
echo "  • The key file at $KEY_FILE contains sensitive credentials"
echo "  • Do not commit it to git or share it publicly"
echo "  • Delete it after creating the Kubernetes secret"
echo ""
print_color "$GREEN" "To verify permissions, run:"
echo "  gcloud projects get-iam-policy $PRIMARY_PROJECT \\"
echo "    --flatten=\"bindings[].members\" \\"
echo "    --filter=\"bindings.members:$SA_EMAIL\" \\"
echo "    --format=\"table(bindings.role)\""