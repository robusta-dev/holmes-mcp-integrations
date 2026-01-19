#!/bin/bash
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; MAGENTA='\033[0;35m'; NC='\033[0m'

CONFIG_FILE="${2:-multi-cluster-config.yaml}"
PERMISSIONS_FILE="${3:-aws-mcp-iam-policy.json}"
ACTION="${1:-}"

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_teardown() { echo -e "${MAGENTA}[TEARDOWN]${NC} $1"; }

usage() {
    echo "Usage: $0 <setup|teardown|verify> [config-file] [permissions-file]"
    echo "Commands: setup, teardown, verify"
    echo "Default config-file: multi-cluster-config.yaml"
    echo "Default permissions-file: aws-mcp-iam-policy.json"
    echo ""
    echo "Note: OIDC provider teardown is skipped by default to prevent accidental deletion."
    echo "      To enable OIDC teardown, set: TEARDOWN_OIDC=true"
    exit 1
}

check_dependencies() {
    log_info "Checking dependencies..."
    for cmd in aws jq yq; do
        if ! command -v "$cmd" &>/dev/null; then
            log_error "Missing: $cmd"
            exit 1
        fi
    done
    log_success "Dependencies OK"
}

check_files() {
    log_info "Checking required files..."
    
    [ ! -f "$CONFIG_FILE" ] && log_error "Config file not found: $CONFIG_FILE" && exit 1
    log_success "Config file found: $CONFIG_FILE"
    
    [ ! -f "$PERMISSIONS_FILE" ] && log_error "Permissions file not found: $PERMISSIONS_FILE" && exit 1
    log_success "Permissions file found: $PERMISSIONS_FILE"
    
    # Validate JSON syntax
    if ! jq empty "$PERMISSIONS_FILE" 2>/dev/null; then
        log_error "Invalid JSON in permissions file: $PERMISSIONS_FILE"
        exit 1
    fi
    log_success "Permissions file JSON syntax valid"
}

load_config() {
    log_info "Loading config..."
    
    K8S_NAMESPACE=$(yq eval '.kubernetes.namespace' "$CONFIG_FILE")
    K8S_SERVICE_ACCOUNT=$(yq eval '.kubernetes.service_account' "$CONFIG_FILE")
    IAM_ROLE_NAME=$(yq eval '.iam.role_name' "$CONFIG_FILE")
    IAM_POLICY_NAME=$(yq eval '.iam.policy_name' "$CONFIG_FILE")
    SESSION_DURATION=$(yq eval '.iam.session_duration' "$CONFIG_FILE")
    CLUSTER_COUNT=$(yq eval '.clusters | length' "$CONFIG_FILE")
    ACCOUNT_COUNT=$(yq eval '.target_accounts | length' "$CONFIG_FILE")
    
    log_success "Loaded: $CLUSTER_COUNT clusters, $ACCOUNT_COUNT accounts"
    log_info "Using permissions file: $PERMISSIONS_FILE"
}

create_trust_policy() {
    local account_id="$1"
    local outfile="$2"
    local i=0
    
    echo '{"Version":"2012-10-17","Statement":[' > "$outfile"
    
    while [ $i -lt $CLUSTER_COUNT ]; do
        local region=$(yq e ".clusters[$i].region" "$CONFIG_FILE")
        local issuer=$(yq e ".clusters[$i].oidc_issuer_id" "$CONFIG_FILE")
        
        [ $i -gt 0 ] && echo "," >> "$outfile"
        
        cat >> "$outfile" <<EOF
{"Effect":"Allow","Principal":{"Federated":"arn:aws:iam::${account_id}:oidc-provider/oidc.eks.${region}.amazonaws.com/id/${issuer}"},"Action":"sts:AssumeRoleWithWebIdentity","Condition":{"StringEquals":{"oidc.eks.${region}.amazonaws.com/id/${issuer}:aud":"sts.amazonaws.com","oidc.eks.${region}.amazonaws.com/id/${issuer}:sub":"system:serviceaccount:${K8S_NAMESPACE}:${K8S_SERVICE_ACCOUNT}"}}}
EOF
        i=$((i+1))
    done
    
    echo ']}' >> "$outfile"
}

create_perms_policy() {
    local outfile="$1"
    log_info "Using external permissions file: $PERMISSIONS_FILE"
    cp "$PERMISSIONS_FILE" "$outfile"
}

setup_oidc() {
    local prof="$1" acct="$2" i=0 created=0 existing=0
    
    log_info "Setting up OIDC..."
    
    while [ $i -lt $CLUSTER_COUNT ]; do
        local name=$(yq e ".clusters[$i].name" "$CONFIG_FILE")
        local region=$(yq e ".clusters[$i].region" "$CONFIG_FILE")
        local issuer=$(yq e ".clusters[$i].oidc_issuer_id" "$CONFIG_FILE")
        # Construct OIDC issuer URL from region and issuer ID (format: https://oidc.eks.{region}.amazonaws.com/id/{issuer_id})
        local url="https://oidc.eks.${region}.amazonaws.com/id/${issuer}"
        local arn="arn:aws:iam::${acct}:oidc-provider/oidc.eks.${region}.amazonaws.com/id/${issuer}"
        
        log_info "  $name"
        
        if aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$arn" --profile "$prof" &>/dev/null; then
            log_warning "    Exists"
            existing=$((existing+1))
        else
            if aws iam create-open-id-connect-provider --url "$url" --client-id-list sts.amazonaws.com \
                --thumbprint-list 9e99a48a9960b14926bb7f3b02e22da2b0ab7280 --profile "$prof" &>/dev/null; then
                log_success "    Created"
                created=$((created+1))
            else
                log_error "    Failed"
                return 1
            fi
        fi
        
        i=$((i+1))
    done
    
    log_success "OIDC: $created created, $existing existing"
}

setup_role() {
    local prof="$1" acct="$2"
    local trust=$(mktemp) perms=$(mktemp)
    
    log_info "Setting up role..."
    trap "rm -f '$trust' '$perms'" RETURN
    
    create_trust_policy "$acct" "$trust"
    create_perms_policy "$perms"
    
        if aws iam get-role --role-name "$IAM_ROLE_NAME" --profile "$prof" &>/dev/null; then
        log_warning "Updating..."
        aws iam update-assume-role-policy --role-name "$IAM_ROLE_NAME" \
            --policy-document "file://$trust" --profile "$prof" &>/dev/null
        log_success "Updated trust policy"
    else
        aws iam create-role --role-name "$IAM_ROLE_NAME" \
            --assume-role-policy-document "file://$trust" \
            --max-session-duration "$SESSION_DURATION" --profile "$prof" &>/dev/null
        log_success "Created role"
    fi
    
    aws iam put-role-policy --role-name "$IAM_ROLE_NAME" \
        --policy-name "$IAM_POLICY_NAME" \
        --policy-document "file://$perms" --profile "$prof" &>/dev/null
    log_success "Applied comprehensive permissions policy"
}

teardown_oidc() {
    local prof="$1" acct="$2" i=0 deleted=0 notfound=0
    
    log_teardown "Removing OIDC..."
    
    while [ $i -lt $CLUSTER_COUNT ]; do
        local name=$(yq e ".clusters[$i].name" "$CONFIG_FILE")
        local region=$(yq e ".clusters[$i].region" "$CONFIG_FILE")
        local issuer=$(yq e ".clusters[$i].oidc_issuer_id" "$CONFIG_FILE")
        local arn="arn:aws:iam::${acct}:oidc-provider/oidc.eks.${region}.amazonaws.com/id/${issuer}"
        
        log_info "  $name"
        
        if ! aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$arn" --profile "$prof" &>/dev/null; then
            log_warning "    Not found"
            notfound=$((notfound+1))
        else
            if aws iam delete-open-id-connect-provider --open-id-connect-provider-arn "$arn" --profile "$prof" &>/dev/null; then
                log_success "    Deleted"
                deleted=$((deleted+1))
            else
                log_error "    Failed"
            fi
        fi
        
        i=$((i+1))
    done
    
    log_success "OIDC: $deleted deleted, $notfound not found"
}

teardown_role() {
    local prof="$1"
    
    log_teardown "Removing role..."
    
    if ! aws iam get-role --role-name "$IAM_ROLE_NAME" --profile "$prof" &>/dev/null; then
        log_warning "Not found"
        return 0
    fi
    
    aws iam delete-role-policy --role-name "$IAM_ROLE_NAME" --policy-name "$IAM_POLICY_NAME" --profile "$prof" &>/dev/null || true
    aws iam delete-role --role-name "$IAM_ROLE_NAME" --profile "$prof" &>/dev/null
    log_success "Deleted"
}

verify_account() {
    local prof="$1" acct="$2" i=0 issues=0
    
    if aws iam get-role --role-name "$IAM_ROLE_NAME" --profile "$prof" &>/dev/null; then
        log_success "✓ Role"
    else
        log_error "✗ Role"
        issues=1
    fi
    
    while [ $i -lt $CLUSTER_COUNT ]; do
        local name=$(yq e ".clusters[$i].name" "$CONFIG_FILE")
        local region=$(yq e ".clusters[$i].region" "$CONFIG_FILE")
        local issuer=$(yq e ".clusters[$i].oidc_issuer_id" "$CONFIG_FILE")
        local arn="arn:aws:iam::${acct}:oidc-provider/oidc.eks.${region}.amazonaws.com/id/${issuer}"
        
        if aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$arn" --profile "$prof" &>/dev/null; then
            log_success "✓ OIDC: $name"
        else
            log_error "✗ OIDC: $name"
            issues=1
        fi
        
        i=$((i+1))
    done
    
    [ $issues -eq 0 ] && log_success "All OK" || log_error "Issues found"
    return $issues
}

process_account() {
    local act="$1" prof="$2" acct="$3" desc="$4"
    
    echo ""
    echo "=========================================="
    log_info "$prof ($acct) - $desc"
    echo "=========================================="
    
    if ! aws sts get-caller-identity --profile "$prof" &>/dev/null; then
        log_error "Cannot access '$prof'"
        return 1
    fi
    
    case "$act" in
        setup)
            setup_oidc "$prof" "$acct" || return 1
            setup_role "$prof" "$acct" || return 1
            ;;
        teardown)
            teardown_role "$prof"
            # Only teardown OIDC if explicitly enabled (OIDC providers may be shared resources)
            if [ "${TEARDOWN_OIDC:-false}" = "true" ]; then
                teardown_oidc "$prof" "$acct"
            else
                log_warning "Skipping OIDC provider teardown (set TEARDOWN_OIDC=true to enable)"
            fi
            ;;
        verify)
            verify_account "$prof" "$acct"
            ;;
    esac
    
    log_success "Done: $prof"
}

main() {
    [ "$ACTION" != "setup" ] && [ "$ACTION" != "teardown" ] && [ "$ACTION" != "verify" ] && usage
    
    echo "=========================================="
    log_info "Multi-Cluster IAM: $ACTION"
    echo "=========================================="
    
    check_dependencies
    check_files
    load_config
    
    local i=0 success=0
    
    while [ $i -lt $ACCOUNT_COUNT ]; do
        local prof=$(yq e ".target_accounts[$i].profile" "$CONFIG_FILE")
        local acct=$(yq e ".target_accounts[$i].account_id" "$CONFIG_FILE")
        local desc=$(yq e ".target_accounts[$i].description" "$CONFIG_FILE")
        
        if process_account "$ACTION" "$prof" "$acct" "$desc"; then
            success=$((success+1))
        fi
        
        i=$((i+1))
    done
    
    echo ""
    echo "=========================================="
    log_success "Successful: $success/$ACCOUNT_COUNT"
    echo "=========================================="
    
    [ $success -eq $ACCOUNT_COUNT ] && log_success "All done!" || exit 1
}

main
