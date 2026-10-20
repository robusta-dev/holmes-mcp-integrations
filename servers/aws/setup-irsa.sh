#!/bin/bash

# IRSA Setup Script for Holmes' access to the hosted AWS MCP Server on EKS
# This script creates the IAM policy and role trusted by the Holmes service account
# Usage: ./setup-irsa.sh --cluster-name <name> --region <region> --namespace <namespace> --service-account <name>

set -e

# Default values
DEFAULT_NAMESPACE="default"
SERVICE_ACCOUNT_NAME="holmes-holmes-service-account"
IAM_POLICY_NAME="holmes-aws-mcp-policy"
IAM_ROLE_BASE="holmes-aws-mcp-role"

# Function to show usage
usage() {
    cat <<EOF
Usage: $0 [OPTIONS]

Setup IRSA (IAM Roles for Service Accounts) so Holmes can call the hosted AWS MCP Server from EKS.

Required Options:
    -c, --cluster-name NAME     EKS cluster name
    -r, --region REGION         AWS region (e.g., us-east-1)

Optional Options:
    -n, --namespace NAMESPACE   Kubernetes namespace where Holmes runs (default: $DEFAULT_NAMESPACE)
    -s, --service-account NAME  Holmes service account (default: $SERVICE_ACCOUNT_NAME,
                                use robusta-holmes-service-account for the Robusta chart)
    --role-base-name NAME       Base name for IAM role (default: $IAM_ROLE_BASE)
    -h, --help                  Show this help message

Examples:
    $0 --cluster-name prod-cluster --region us-east-1
    $0 -c dev-cluster -r us-west-2 -n robusta -s robusta-holmes-service-account
    $0 --cluster-name staging --region eu-west-1 --namespace monitoring --role-base-name custom-holmes-role

EOF
    exit ${1:-0}
}

# Parse command line arguments
CLUSTER_NAME=""
AWS_REGION=""
NAMESPACE="$DEFAULT_NAMESPACE"

while [[ $# -gt 0 ]]; do
    case $1 in
        -c|--cluster-name)
            CLUSTER_NAME="$2"
            shift 2
            ;;
        -r|--region)
            AWS_REGION="$2"
            shift 2
            ;;
        -n|--namespace)
            NAMESPACE="$2"
            shift 2
            ;;
        -s|--service-account)
            SERVICE_ACCOUNT_NAME="$2"
            shift 2
            ;;
        --role-base-name)
            IAM_ROLE_BASE="$2"
            shift 2
            ;;
        -h|--help)
            usage 0
            ;;
        *)
            echo "❌ Unknown option: $1"
            echo ""
            usage 1
            ;;
    esac
done

# Validate required arguments
if [ -z "$CLUSTER_NAME" ]; then
    echo "❌ Error: --cluster-name is required"
    echo ""
    usage 1
fi

if [ -z "$AWS_REGION" ]; then
    echo "❌ Error: --region is required"
    echo ""
    usage 1
fi

echo "🚀 Setting up IRSA for Holmes AWS MCP access"
echo "   Cluster: $CLUSTER_NAME"
echo "   Region: $AWS_REGION"
echo "   Namespace: $NAMESPACE"
echo "   Service Account: $SERVICE_ACCOUNT_NAME"
echo ""

# Get AWS account ID
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "✅ AWS Account ID: $AWS_ACCOUNT_ID"

# Check if OIDC provider exists for the cluster
echo "🔍 Checking for OIDC provider..."
OIDC_ID=$(aws eks describe-cluster --name "$CLUSTER_NAME" --region "$AWS_REGION" --query "cluster.identity.oidc.issuer" --output text | cut -d '/' -f 5)

if [ -z "$OIDC_ID" ]; then
    echo "❌ No OIDC provider found for cluster $CLUSTER_NAME"
    echo "   Please ensure the cluster exists and you have access to it."
    exit 1
fi
echo "✅ Found OIDC provider: $OIDC_ID"

# Check if OIDC provider is associated with IAM
OIDC_PROVIDER_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:oidc-provider/oidc.eks.${AWS_REGION}.amazonaws.com/id/${OIDC_ID}"
if ! aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$OIDC_PROVIDER_ARN" >/dev/null 2>&1; then
    echo "⚠️  OIDC provider not associated with IAM. Creating..."

    # Check if eksctl is available
    if ! command -v eksctl &> /dev/null; then
        echo "❌ eksctl is required to create OIDC provider but is not installed"
        echo "   Please install eksctl or manually create the OIDC provider"
        exit 1
    fi

    # Create OIDC provider using eksctl
    eksctl utils associate-iam-oidc-provider \
        --cluster "$CLUSTER_NAME" \
        --region "$AWS_REGION" \
        --approve

    echo "✅ OIDC provider created and associated with IAM"
else
    echo "✅ OIDC provider already associated with IAM"
fi
echo ""

# Step 2: Create or check IAM policy
echo "📝 Checking IAM policy: $IAM_POLICY_NAME"
POLICY_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:policy/${IAM_POLICY_NAME}"

# Check if the policy JSON file exists
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POLICY_FILE="${SCRIPT_DIR}/aws-mcp-iam-policy.json"

if [ ! -f "$POLICY_FILE" ]; then
    echo "❌ Policy file not found: $POLICY_FILE"
    echo "   Please ensure the aws-mcp-iam-policy.json file is in the same directory as this script"
    exit 1
fi

if aws iam get-policy --policy-arn "$POLICY_ARN" >/dev/null 2>&1; then
    echo "✅ Using existing IAM policy: $IAM_POLICY_NAME"
    echo "   ARN: $POLICY_ARN"
else
    echo "📝 Creating new IAM policy: $IAM_POLICY_NAME"
    aws iam create-policy \
        --policy-name "$IAM_POLICY_NAME" \
        --policy-document file://"$POLICY_FILE" \
        --description "IAM policy for Holmes AWS MCP server - comprehensive read-only access" >/dev/null
    echo "✅ IAM policy created"
    echo "   ARN: $POLICY_ARN"
fi
echo ""

# Step 3: Create IAM role with auto-suffix if needed
echo "🔐 Creating IAM role..."

# Find an available role name
IAM_ROLE_NAME="${IAM_ROLE_BASE}-${CLUSTER_NAME}"
SUFFIX=1

while aws iam get-role --role-name "$IAM_ROLE_NAME" >/dev/null 2>&1; do
    echo "   Role '$IAM_ROLE_NAME' already exists, trying with suffix..."
    IAM_ROLE_NAME="${IAM_ROLE_BASE}-${CLUSTER_NAME}-${SUFFIX}"
    ((SUFFIX++))

    if [ $SUFFIX -gt 10 ]; then
        echo "❌ Too many existing roles with base name: $IAM_ROLE_BASE-$CLUSTER_NAME"
        echo "   Please clean up old roles or use a different --role-base-name"
        exit 1
    fi
done

echo "✅ Using IAM role name: $IAM_ROLE_NAME"

# Create trust policy for the role
OIDC_URL="oidc.eks.${AWS_REGION}.amazonaws.com/id/${OIDC_ID}"
TRUST_POLICY_FILE="/tmp/holmes-trust-policy-$$.json"

cat > "$TRUST_POLICY_FILE" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::${AWS_ACCOUNT_ID}:oidc-provider/${OIDC_URL}"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "${OIDC_URL}:aud": "sts.amazonaws.com",
          "${OIDC_URL}:sub": "system:serviceaccount:${NAMESPACE}:${SERVICE_ACCOUNT_NAME}"
        }
      }
    }
  ]
}
EOF

# Create the IAM role
echo "   Creating IAM role with trust policy..."
aws iam create-role \
    --role-name "$IAM_ROLE_NAME" \
    --assume-role-policy-document file://"$TRUST_POLICY_FILE" \
    --description "IAM role for Holmes AWS MCP server on cluster $CLUSTER_NAME" >/dev/null

# Attach the policy to the role
echo "   Attaching policy to role..."
aws iam attach-role-policy \
    --role-name "$IAM_ROLE_NAME" \
    --policy-arn "$POLICY_ARN"

# Clean up temp file
rm -f "$TRUST_POLICY_FILE"

echo "✅ IAM role created and configured"

# Get the role ARN
ROLE_ARN=$(aws iam get-role --role-name "$IAM_ROLE_NAME" --query 'Role.Arn' --output text)
echo "   Role ARN: $ROLE_ARN"
echo ""

# Verify the setup
echo "📋 Setup Summary:"
echo "   ✅ IAM Policy: $IAM_POLICY_NAME"
echo "   ✅ IAM Role: $IAM_ROLE_NAME"
echo "   ✅ Trusted ServiceAccount: $SERVICE_ACCOUNT_NAME (namespace: $NAMESPACE)"
echo "   ✅ Role ARN: $ROLE_ARN"
echo ""

echo "🎉 IRSA setup complete!"
echo ""
echo "📋 Next steps:"
echo "1. Annotate the Holmes service account in your Helm values and enable the addon:"
echo "   serviceAccount:"
echo "     annotations:"
echo "       eks.amazonaws.com/role-arn: $ROLE_ARN"
echo "   mcpAddons:"
echo "     aws:"
echo "       enabled: true"
echo "       config:"
echo "         region: $AWS_REGION"
echo ""
echo "2. Deploy Holmes with Helm (nest the values under holmes: for the Robusta chart)"
echo ""
echo "💡 To verify the setup after deploying, run:"
echo "   kubectl exec -n $NAMESPACE deploy/holmes-holmes -- python -c \"import boto3; print(boto3.client('sts').get_caller_identity()['Arn'])\""
