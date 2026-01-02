#!/bin/sh
# Entrypoint to configure gcloud CLI authentication
# Supports both service account key and fallback to default authentication

# Check for service account key file (preferred method)
if [ -f "/var/secrets/gcp/key.json" ]; then
    echo "Found service account key, authenticating..."

    # Authenticate using the service account key
    if gcloud auth activate-service-account --key-file=/var/secrets/gcp/key.json --quiet 2>&1; then
        echo "✓ Successfully authenticated with service account key"

        # Also set for any Google Cloud SDKs
        export GOOGLE_APPLICATION_CREDENTIALS=/var/secrets/gcp/key.json

        # Get the authenticated account
        ACCOUNT=$(gcloud config get-value account 2>/dev/null)
        echo "✓ Authenticated as: $ACCOUNT"

        # Enable multi-project mode
        echo "ℹ Multi-project mode enabled. Use --project flag to query specific projects."

        # List accessible projects if possible
        echo "ℹ Attempting to list accessible projects..."
        gcloud projects list --format="value(projectId)" 2>/dev/null | head -5 | while read -r project; do
            echo "  - $project"
        done
    else
        echo "⚠ Failed to authenticate with service account key"
        echo "Falling back to default authentication method"
    fi
elif [ -n "$GOOGLE_APPLICATION_CREDENTIALS" ]; then
    echo "Using existing GOOGLE_APPLICATION_CREDENTIALS: $GOOGLE_APPLICATION_CREDENTIALS"
else
    echo "No service account key found at /var/secrets/gcp/key.json"
    echo "Using default authentication (node service account or metadata server)"
    echo ""
    echo "To use service account authentication:"
    echo "1. Create a service account key:"
    echo "   gcloud iam service-accounts keys create key.json \\"
    echo "     --iam-account=YOUR_SA@PROJECT.iam.gserviceaccount.com"
    echo ""
    echo "2. Create a Kubernetes secret:"
    echo "   kubectl create secret generic gcp-sa-key \\"
    echo "     --from-file=key.json=key.json \\"
    echo "     -n YOUR_NAMESPACE"
    echo ""
    echo "3. Configure Helm values to reference the secret"
fi

# Execute the original command
exec "$@"