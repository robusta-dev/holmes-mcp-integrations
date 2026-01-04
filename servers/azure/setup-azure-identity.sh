#!/bin/bash

# Setup script for Azure MCP Server Authentication
# Supports: Managed Identity, Service Principal, and Workload Identity
#
# This script is IDEMPOTENT - safe to run multiple times!
# - Checks for existing resources before creating
# - Skips already configured components
# - Useful for retrying after timeouts or partial failures

# Don't exit on error - we handle errors explicitly
set +e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default values
AUTH_METHOD=""
RESOURCE_GROUP=""
AKS_CLUSTER=""
NAMESPACE="default"
SERVICE_ACCOUNT="azure-api-mcp-sa"
SUBSCRIPTION_LIST=""
ALL_SUBSCRIPTIONS="false"

# Function to print colored output
print_color() {
    color=$1
    message=$2
    echo -e "${color}${message}${NC}"
}

# Function to print usage
usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --auth-method METHOD     Authentication method:"
    echo "                            - workload-identity: For AKS (recommended)"
    echo "                            - service-principal: For non-AKS K8s or local testing"
    echo "                            - managed-identity: For Azure VMs/VMSS"
    echo "  --resource-group RG      Azure resource group where managed identity will be created (for workload-identity)"
    echo "  --aks-cluster CLUSTER    AKS cluster name (required for workload-identity)"
    echo "  --namespace NS           Kubernetes namespace (default: default)"
    echo "  --service-account SA     Kubernetes service account name (default: azure-api-mcp-sa)"
    echo "  --tenant TENANT          Azure tenant ID"
    echo "  --subscriptions LIST     Comma-separated list of subscription IDs (or single subscription)"
    echo "  --all-subscriptions      Apply to all accessible subscriptions"
    echo "  --help                   Show this help message"
    echo ""
    echo "Examples:"
    echo "  # Setup workload identity for AKS (recommended for AKS clusters)"
    echo "  $0 --auth-method workload-identity --resource-group myRG --aks-cluster myAKS"
    echo ""
    echo "  # Setup for specific subscription"
    echo "  $0 --auth-method workload-identity --resource-group myRG --aks-cluster myAKS --subscriptions \"sub-id\""
    echo ""
    echo "  # Setup for multiple subscriptions"
    echo "  $0 --auth-method workload-identity --resource-group myRG --aks-cluster myAKS --subscriptions \"sub1,sub2,sub3\""
    echo ""
    echo "  # Setup service principal for all subscriptions (for non-AKS environments)"
    echo "  $0 --auth-method service-principal --all-subscriptions"
    exit 1
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --auth-method)
            AUTH_METHOD="$2"
            shift 2
            ;;
        --resource-group)
            RESOURCE_GROUP="$2"
            shift 2
            ;;
        --aks-cluster)
            AKS_CLUSTER="$2"
            shift 2
            ;;
        --namespace)
            NAMESPACE="$2"
            shift 2
            ;;
        --service-account)
            SERVICE_ACCOUNT="$2"
            shift 2
            ;;
        --tenant)
            TENANT="$2"
            shift 2
            ;;
        --subscriptions)
            SUBSCRIPTION_LIST="$2"
            shift 2
            ;;
        --all-subscriptions)
            ALL_SUBSCRIPTIONS="true"
            shift
            ;;
        --help)
            usage
            ;;
        *)
            print_color $RED "Unknown option: $1"
            usage
            ;;
    esac
done

# Validate auth method
if [[ -z "$AUTH_METHOD" ]]; then
    print_color $RED "Error: --auth-method is required"
    usage  # This exits
fi

# Check if Azure CLI is installed
if ! command -v az &> /dev/null; then
    print_color $RED "Error: Azure CLI is not installed. Please install it first."
    exit 1
fi

# Check if jq is installed (needed for JSON processing)
if ! command -v jq &> /dev/null; then
    print_color $RED "Error: jq is not installed. Please install it first."
    exit 1
fi

# Login check
if ! az account show &> /dev/null; then
    print_color $YELLOW "Not logged in to Azure. Please login first:"
    az login
fi

# Get current subscription info
CURRENT_SUB=$(az account show --query id -o tsv)
CURRENT_TENANT=$(az account show --query tenantId -o tsv)

print_color $GREEN "Current Subscription: $CURRENT_SUB"
print_color $GREEN "Current Tenant: $CURRENT_TENANT"

# Determine target subscriptions
if [[ "$ALL_SUBSCRIPTIONS" == "true" ]]; then
    print_color $YELLOW "Using all accessible subscriptions..."
    SUBSCRIPTIONS=$(az account list --query "[].id" -o tsv)
elif [[ -n "$SUBSCRIPTION_LIST" ]]; then
    print_color $YELLOW "Using specified subscriptions..."
    IFS=',' read -ra SUBSCRIPTIONS <<< "$SUBSCRIPTION_LIST"
else
    print_color $YELLOW "Using current subscription only..."
    SUBSCRIPTIONS=("$CURRENT_SUB")
fi

# Global arrays to track results
SUCCESSFUL_SUBS=()
FAILED_SUBS=()

# Function to create custom role and assign it
setup_custom_role() {
    local identity_client_id=$1
    local identity_object_id=$2  # Add object ID as second parameter
    local role_name="Azure MCP Reader"
    local script_dir="$(cd "$(dirname "$0")" && pwd)"
    local role_json_file="$script_dir/azure-rbac-roles.json"

    # Check if the role definition JSON file exists
    if [[ ! -f "$role_json_file" ]]; then
        print_color $RED "Error: azure-rbac-roles.json not found in $script_dir"
        return 1
    fi

    print_color $YELLOW "Setting up custom role: $role_name"

    # Note for cross-subscription scenarios
    if [ ${#SUBSCRIPTIONS[@]} -gt 1 ]; then
        print_color $YELLOW "Note: For managed identities, cross-subscription role assignments may require additional permissions."
        print_color $YELLOW "Ensure you have Owner or User Access Administrator role on target subscriptions."
    fi

    # First, check if the custom role exists anywhere (it's a tenant-level name)
    ROLE_EXISTS_ANYWHERE=$(az role definition list --name "$role_name" --query "[0].name" -o tsv 2>/dev/null)

    if [ -z "$ROLE_EXISTS_ANYWHERE" ]; then
        # Role doesn't exist at all, create it ONCE with ALL subscriptions in AssignableScopes
        print_color $YELLOW "Creating custom role with all subscriptions in scope..."

        # Build the AssignableScopes array with ALL subscriptions
        SCOPES_JSON='['
        first=true
        for s in ${SUBSCRIPTIONS[@]}; do
            if [ "$first" = true ]; then
                first=false
            else
                SCOPES_JSON="$SCOPES_JSON,"
            fi
            SCOPES_JSON="$SCOPES_JSON\"/subscriptions/$s\""
        done
        SCOPES_JSON="$SCOPES_JSON]"

        # Create a temporary JSON file with ALL subscriptions in AssignableScopes
        TEMP_JSON=$(mktemp)
        jq ".AssignableScopes = $SCOPES_JSON" "$role_json_file" > "$TEMP_JSON"

        # Create the role (it will be available in all subscriptions)
        ERROR_MSG=$(az role definition create --role-definition "$TEMP_JSON" 2>&1)
        if [ $? -eq 0 ]; then
            print_color $GREEN "✅ Custom role created successfully with all subscriptions in scope!"
        else
            print_color $RED "Failed to create custom role"
            print_color $RED "Error: $ERROR_MSG"
            print_color $YELLOW "Falling back to built-in Reader role"
            role_name="Reader"
        fi
        rm -f "$TEMP_JSON"

        # Give Azure some time to propagate the new role
        sleep 5
    else
        print_color $GREEN "Custom role '$role_name' already exists"
    fi

    # Now assign the role to each subscription
    for sub in ${SUBSCRIPTIONS[@]}; do
        print_color $YELLOW "Processing subscription: $sub"
        local sub_success=true

        # Check if role assignment already exists (try both client ID and object ID)
        local assignment_exists=false

        # Use 2>/dev/null instead of &>/dev/null to only suppress errors
        EXISTING_ASSIGNMENT=$(az role assignment list \
            --assignee "$identity_client_id" \
            --role "$role_name" \
            --scope "/subscriptions/$sub" \
            --query "[0].id" -o tsv 2>/dev/null)

        if [ -n "$EXISTING_ASSIGNMENT" ]; then
            assignment_exists=true
        elif [ -n "$identity_object_id" ]; then
            EXISTING_ASSIGNMENT=$(az role assignment list \
                --assignee "$identity_object_id" \
                --role "$role_name" \
                --scope "/subscriptions/$sub" \
                --query "[0].id" -o tsv 2>/dev/null)
            if [ -n "$EXISTING_ASSIGNMENT" ]; then
                assignment_exists=true
            fi
        fi

        if [ "$assignment_exists" = true ]; then
            print_color $GREEN "Role assignment already exists for subscription: $sub"
        else
            # Assign the custom role with retry logic for Azure AD propagation
            print_color $YELLOW "Assigning custom role to identity..."

            local max_retries=5
            local retry_count=0
            local wait_time=10

            while [ $retry_count -lt $max_retries ]; do
                # Try with client ID first, then object ID if available
                local assignee_id="$identity_client_id"

                # If we have an object ID and this is not the first attempt, try with object ID
                if [ $retry_count -gt 2 ] && [ -n "$identity_object_id" ]; then
                    assignee_id="$identity_object_id"
                    print_color $YELLOW "Trying with object ID instead of client ID..."
                fi

                # Capture error output
                ERROR_OUTPUT=$(az role assignment create \
                    --assignee "$assignee_id" \
                    --role "$role_name" \
                    --scope "/subscriptions/$sub" 2>&1)

                if [ $? -eq 0 ]; then
                    print_color $GREEN "Role assigned successfully!"
                    break
                else
                    retry_count=$((retry_count + 1))
                    if [ $retry_count -eq $max_retries ]; then
                        print_color $RED "Failed to assign role after $max_retries attempts"
                        print_color $RED "Error: $ERROR_OUTPUT"
                        print_color $YELLOW "You may need to run the script again or manually assign the role"
                        print_color $YELLOW "Manual command: az role assignment create --assignee $identity_object_id --role \"$role_name\" --scope \"/subscriptions/$sub\""
                    else
                        # Check if it's a "not found" error vs other errors
                        if echo "$ERROR_OUTPUT" | grep -q "Cannot find user or service principal"; then
                            print_color $YELLOW "Waiting for Azure AD propagation... (attempt $retry_count/$max_retries)"
                            sleep $wait_time
                            wait_time=$((wait_time * 2))  # Exponential backoff
                        else
                            print_color $RED "Error: $ERROR_OUTPUT"
                            sub_success=false
                            break
                        fi
                    fi
                fi
            done

            if [ $retry_count -eq $max_retries ]; then
                sub_success=false
            fi
        fi  # End of role assignment check

        # Track results
        if [ "$sub_success" = true ]; then
            SUCCESSFUL_SUBS+=("$sub")
        else
            FAILED_SUBS+=("$sub")
        fi
    done

    # Report summary
    print_color $GREEN "=========================================="
    print_color $GREEN "Role Setup Summary:"
    print_color $GREEN "=========================================="

    if [ ${#SUCCESSFUL_SUBS[@]} -gt 0 ]; then
        print_color $GREEN "✅ Successfully configured subscriptions: ${#SUCCESSFUL_SUBS[@]}"
        for sub in "${SUCCESSFUL_SUBS[@]}"; do
            echo "   - $sub"
        done
    fi

    if [ ${#FAILED_SUBS[@]} -gt 0 ]; then
        print_color $YELLOW "⚠️  Failed subscriptions: ${#FAILED_SUBS[@]}"
        for sub in "${FAILED_SUBS[@]}"; do
            echo "   - $sub"
        done
        print_color $YELLOW "Note: Sometimes operations timeout due to Azure propagation delays. Try running the script again."
        print_color $YELLOW "For failed subscriptions, you may need to manually run:"
        print_color $YELLOW "az role assignment create --assignee $identity_client_id --role \"$role_name\" --scope \"/subscriptions/SUB_ID\""
        print_color $YELLOW "(The assignee ID is already filled in: $identity_client_id)"
        print_color $YELLOW "Replace SUB_ID with the actual subscription ID from the list above"
    fi
}

# Setup based on auth method
case $AUTH_METHOD in
    managed-identity)
        print_color $YELLOW "Setting up Managed Identity..."

        # For managed identity on Azure VMs or AKS
        print_color $GREEN "Managed Identity setup:"
        echo "1. Ensure your Azure VM or AKS has system-assigned managed identity enabled"
        echo "2. Assign the following roles to the managed identity:"
        echo "   - Reader on the subscription or specific resource groups"
        echo "   - Log Analytics Reader (for querying logs)"
        echo "   - Monitoring Reader (for metrics)"
        echo ""
        echo "For AKS nodes:"
        echo "  az aks update -g $RESOURCE_GROUP -n $AKS_CLUSTER --enable-managed-identity"
        ;;

    service-principal)
        print_color $YELLOW "Setting up Service Principal..."

        # Create service principal
        SP_NAME="azure-mcp-sp-$(date +%s)"
        print_color $YELLOW "Creating service principal: $SP_NAME"

        # Create SP without any role assignment (we'll add custom role next)
        SP_OUTPUT=$(az ad sp create-for-rbac --name "$SP_NAME" --skip-assignment)

        APP_ID=$(echo "$SP_OUTPUT" | grep -o '"appId": "[^"]*' | grep -o '[^"]*$')
        PASSWORD=$(echo "$SP_OUTPUT" | grep -o '"password": "[^"]*' | grep -o '[^"]*$')
        TENANT_ID=$(echo "$SP_OUTPUT" | grep -o '"tenant": "[^"]*' | grep -o '[^"]*$')

        # Setup custom role for the service principal (pass empty string for object ID)
        setup_custom_role "$APP_ID" ""

        print_color $GREEN "Service Principal created successfully!"
        echo ""
        echo "Save these credentials securely:"
        echo "----------------------------------------"
        echo "AZURE_CLIENT_ID=$APP_ID"
        echo "AZURE_CLIENT_SECRET=$PASSWORD"
        echo "AZURE_TENANT_ID=$TENANT_ID"
        echo "AZURE_SUBSCRIPTION_ID=$CURRENT_SUB"
        echo "----------------------------------------"
        echo ""
        echo "Create Kubernetes secret:"
        echo "kubectl create secret generic azure-mcp-creds \\"
        echo "  --from-literal=AZURE_CLIENT_ID=$APP_ID \\"
        echo "  --from-literal=AZURE_CLIENT_SECRET=$PASSWORD \\"
        echo "  --from-literal=AZURE_TENANT_ID=$TENANT_ID \\"
        echo "  --from-literal=AZURE_SUBSCRIPTION_ID=$CURRENT_SUB \\"
        echo "  -n $NAMESPACE"
        ;;

    workload-identity)
        print_color $YELLOW "Setting up Workload Identity for AKS..."

        if [[ -z "$RESOURCE_GROUP" || -z "$AKS_CLUSTER" ]]; then
            print_color $RED "Error: --resource-group and --aks-cluster are required for workload identity"
            exit 1
        fi

        # Enable workload identity on AKS cluster
        print_color $YELLOW "Enabling workload identity on AKS cluster..."
        az aks update -g "$RESOURCE_GROUP" -n "$AKS_CLUSTER" --enable-oidc-issuer --enable-workload-identity

        # Get OIDC issuer URL
        OIDC_ISSUER=$(az aks show -n "$AKS_CLUSTER" -g "$RESOURCE_GROUP" --query "oidcIssuerProfile.issuerUrl" -o tsv)
        print_color $GREEN "OIDC Issuer URL: $OIDC_ISSUER"

        # Create or get managed identity
        IDENTITY_NAME="azure-mcp-identity"

        # Check if identity already exists
        EXISTING_IDENTITY=$(az identity show --name "$IDENTITY_NAME" --resource-group "$RESOURCE_GROUP" --query name -o tsv 2>/dev/null)
        if [ -n "$EXISTING_IDENTITY" ]; then
            print_color $GREEN "Managed identity already exists: $IDENTITY_NAME"
            CLIENT_ID=$(az identity show --name "$IDENTITY_NAME" --resource-group "$RESOURCE_GROUP" --query clientId -o tsv)
            OBJECT_ID=$(az identity show --name "$IDENTITY_NAME" --resource-group "$RESOURCE_GROUP" --query principalId -o tsv)
            RESOURCE_ID=$(az identity show --name "$IDENTITY_NAME" --resource-group "$RESOURCE_GROUP" --query id -o tsv)
        else
            print_color $YELLOW "Creating managed identity: $IDENTITY_NAME"
            az identity create --name "$IDENTITY_NAME" --resource-group "$RESOURCE_GROUP"
            CLIENT_ID=$(az identity show --name "$IDENTITY_NAME" --resource-group "$RESOURCE_GROUP" --query clientId -o tsv)
            OBJECT_ID=$(az identity show --name "$IDENTITY_NAME" --resource-group "$RESOURCE_GROUP" --query principalId -o tsv)
            RESOURCE_ID=$(az identity show --name "$IDENTITY_NAME" --resource-group "$RESOURCE_GROUP" --query id -o tsv)

            # Wait for new identity to propagate in Azure AD
            print_color $YELLOW "Waiting for managed identity to propagate in Azure AD..."
            sleep 15
        fi

        # Setup custom role for the managed identity
        setup_custom_role "$CLIENT_ID" "$OBJECT_ID"

        # Create federated credential (CRITICAL - must always run)
        print_color $YELLOW "=========================================="
        print_color $YELLOW "Creating federated credential (CRITICAL STEP)..."
        print_color $YELLOW "=========================================="

        # Check if federated credential already exists
        EXISTING_FED_CRED=$(az identity federated-credential show \
            --name "azure-mcp-federated" \
            --identity-name "$IDENTITY_NAME" \
            --resource-group "$RESOURCE_GROUP" \
            --query name -o tsv 2>/dev/null)

        if [ -n "$EXISTING_FED_CRED" ]; then
            print_color $GREEN "Federated credential already exists"
        else
            if az identity federated-credential create \
                --name "azure-mcp-federated" \
                --identity-name "$IDENTITY_NAME" \
                --resource-group "$RESOURCE_GROUP" \
                --issuer "$OIDC_ISSUER" \
                --subject "system:serviceaccount:${NAMESPACE}:${SERVICE_ACCOUNT}" \
                --audience api://AzureADTokenExchange 2>/dev/null; then
                print_color $GREEN "✅ Federated credential created successfully!"
            else
                print_color $RED "❌ Failed to create federated credential - this is critical for workload identity!"
                print_color $YELLOW "Manual command to fix:"
                print_color $YELLOW "az identity federated-credential create --name \"azure-mcp-federated\" --identity-name \"$IDENTITY_NAME\" --resource-group \"$RESOURCE_GROUP\" --issuer \"$OIDC_ISSUER\" --subject \"system:serviceaccount:${NAMESPACE}:${SERVICE_ACCOUNT}\" --audience api://AzureADTokenExchange"
            fi
        fi

        print_color $GREEN "Workload Identity setup complete!"
        echo ""
        echo "Update your Helm values:"
        echo "----------------------------------------"
        echo "mcpAddons:"
        echo "  azure:"
        echo "    serviceAccount:"
        echo "      annotations:"
        echo "        azure.workload.identity/client-id: $CLIENT_ID"
        echo "        azure.workload.identity/tenant-id: $CURRENT_TENANT"
        echo "    config:"
        echo "      tenantId: $CURRENT_TENANT"
        echo "      subscriptionId: $CURRENT_SUB"
        echo "      authMethod: workload-identity"
        echo "----------------------------------------"

        # Create service account with annotations (CRITICAL - must always run)
        print_color $YELLOW "=========================================="
        print_color $YELLOW "Creating Kubernetes service account..."
        print_color $YELLOW "=========================================="

        # Check if kubectl is available
        if command -v kubectl &> /dev/null; then
            if kubectl create serviceaccount "$SERVICE_ACCOUNT" -n "$NAMESPACE" --dry-run=client -o yaml | \
                kubectl annotate -f - \
                azure.workload.identity/client-id="$CLIENT_ID" \
                azure.workload.identity/tenant-id="$CURRENT_TENANT" \
                --local -o yaml | \
                kubectl apply -f - 2>/dev/null; then

                # Label the service account
                kubectl label serviceaccount "$SERVICE_ACCOUNT" -n "$NAMESPACE" \
                    azure.workload.identity/use=true --overwrite

                print_color $GREEN "✅ Kubernetes service account created/updated successfully!"
            else
                print_color $YELLOW "⚠️  Could not create Kubernetes service account - ensure kubectl is configured"
                print_color $YELLOW "Manual commands to run:"
                echo "kubectl create serviceaccount $SERVICE_ACCOUNT -n $NAMESPACE"
                echo "kubectl annotate serviceaccount $SERVICE_ACCOUNT -n $NAMESPACE azure.workload.identity/client-id=\"$CLIENT_ID\""
                echo "kubectl annotate serviceaccount $SERVICE_ACCOUNT -n $NAMESPACE azure.workload.identity/tenant-id=\"$CURRENT_TENANT\""
                echo "kubectl label serviceaccount $SERVICE_ACCOUNT -n $NAMESPACE azure.workload.identity/use=true"
            fi
        else
            print_color $YELLOW "kubectl not found - skipping service account creation"
            print_color $YELLOW "Install kubectl and run these commands:"
            echo "kubectl create serviceaccount $SERVICE_ACCOUNT -n $NAMESPACE"
            echo "kubectl annotate serviceaccount $SERVICE_ACCOUNT -n $NAMESPACE azure.workload.identity/client-id=\"$CLIENT_ID\""
            echo "kubectl annotate serviceaccount $SERVICE_ACCOUNT -n $NAMESPACE azure.workload.identity/tenant-id=\"$CURRENT_TENANT\""
            echo "kubectl label serviceaccount $SERVICE_ACCOUNT -n $NAMESPACE azure.workload.identity/use=true"
        fi
        ;;

    *)
        print_color $RED "Invalid auth method: $AUTH_METHOD"
        usage
        ;;
esac

# Final Summary
print_color $GREEN "=========================================="
print_color $GREEN "🎉 Azure Identity Setup Summary"
print_color $GREEN "=========================================="

if [[ "$AUTH_METHOD" == "workload-identity" ]]; then
    echo "Authentication Method: Workload Identity"
    echo "Identity Name: azure-mcp-identity"
    echo "Resource Group: $RESOURCE_GROUP"
    echo "AKS Cluster: $AKS_CLUSTER"
    echo ""

    # Check what was created
    echo "Components Status:"

    # Check managed identity
    EXISTING_ID=$(az identity show --name "azure-mcp-identity" --resource-group "$RESOURCE_GROUP" --query name -o tsv 2>/dev/null)
    if [ -n "$EXISTING_ID" ]; then
        echo "✅ Managed Identity: Created"
    else
        echo "❌ Managed Identity: Not found"
    fi

    # Check federated credential
    EXISTING_FED=$(az identity federated-credential show --name "azure-mcp-federated" --identity-name "azure-mcp-identity" --resource-group "$RESOURCE_GROUP" --query name -o tsv 2>/dev/null)
    if [ -n "$EXISTING_FED" ]; then
        echo "✅ Federated Credential: Configured"
    else
        echo "❌ Federated Credential: Not configured"
    fi

    # Check service account if kubectl available
    if command -v kubectl &> /dev/null; then
        SA_EXISTS=$(kubectl get sa "$SERVICE_ACCOUNT" -n "$NAMESPACE" --no-headers 2>/dev/null)
        if [ -n "$SA_EXISTS" ]; then
            echo "✅ Kubernetes Service Account: Created"
        else
            echo "⚠️  Kubernetes Service Account: Not found"
        fi
    else
        echo "⚠️  Kubernetes Service Account: kubectl not available to check"
    fi

    echo ""
fi

if [ ${#SUCCESSFUL_SUBS[@]} -gt 0 ] || [ ${#FAILED_SUBS[@]} -gt 0 ]; then
    echo "Subscription Configuration:"
    if [ ${#SUCCESSFUL_SUBS[@]} -gt 0 ]; then
        echo "✅ Configured: ${#SUCCESSFUL_SUBS[@]} subscriptions"
    fi
    if [ ${#FAILED_SUBS[@]} -gt 0 ]; then
        echo "⚠️  Failed: ${#FAILED_SUBS[@]} subscriptions (see details above)"
    fi
    echo ""
fi

print_color $YELLOW "Next steps:"
echo "1. Build and push the Docker image:"
echo "   ./build-and-push.sh --registry YOUR_REGISTRY"
echo "2. Deploy the Azure MCP server to Kubernetes"
echo "3. Configure Holmes with the MCP server endpoint"

if [ ${#FAILED_SUBS[@]} -gt 0 ]; then
    echo ""
    print_color $YELLOW "⚠️  Some subscriptions failed - review the errors above"
    print_color $YELLOW "Tip: Often this is due to timeouts. Try running the script again - it's safe to rerun!"
fi