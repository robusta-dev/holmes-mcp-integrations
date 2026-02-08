#!/bin/bash

# Build and push all MCP servers to the new registry
# Registry: us-central1-docker.pkg.dev/genuine-flight-317411/mcp/
#
# Each server directory must have an auto-build-config.yaml with:
#   image: <image-name>
#   version: "<version>"
#
# Usage:
#   ./build-all-mcp-servers.sh           # Build all servers
#   ./build-all-mcp-servers.sh --dry-run # List servers and check registry (no build)
#   ./build-all-mcp-servers.sh --force   # Build even if image exists

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY="us-central1-docker.pkg.dev/genuine-flight-317411/mcp"
BASE_IMAGE_DIR="mcp_base_image"
SERVERS_BASE_DIR="servers"

# Options
DRY_RUN=false
FORCE_BUILD=false

# Track results
declare -a BUILT_IMAGES=()
declare -a SKIPPED_IMAGES=()
declare -a PENDING_IMAGES=()

#######################################
# Print usage information
#######################################
usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --dry-run    List all servers and check which images exist (no build)"
    echo "  --force      Build even if image already exists in registry"
    echo "  --help       Show this help message"
    echo ""
    echo "Registry: $REGISTRY"
}

#######################################
# Parse command line arguments
#######################################
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --dry-run)
                DRY_RUN=true
                shift
                ;;
            --force)
                FORCE_BUILD=true
                shift
                ;;
            --help|-h)
                usage
                exit 0
                ;;
            *)
                echo "Unknown option: $1"
                usage
                exit 1
                ;;
        esac
    done
}

#######################################
# Parse auto-build-config.yaml and set IMAGE and VERSION variables
# Arguments:
#   $1 - path to config file
# Returns:
#   0 on success, 1 on failure
#######################################
parse_config() {
    local config_file="$1"

    if [[ ! -f "$config_file" ]]; then
        echo "ERROR: Config file not found: $config_file"
        return 1
    fi

    IMAGE=$(grep '^image:' "$config_file" | sed 's/image: *//')
    VERSION=$(grep '^version:' "$config_file" | sed 's/version: *//' | tr -d '"')

    if [[ -z "$IMAGE" || -z "$VERSION" ]]; then
        echo "ERROR: Invalid config (missing image or version): $config_file"
        return 1
    fi

    return 0
}

#######################################
# Check if an image:tag already exists in the registry
# Arguments:
#   $1 - image name
#   $2 - version/tag
# Returns:
#   0 if exists, 1 if not exists
#######################################
image_exists() {
    local image="$1"
    local version="$2"
    local full_image="$REGISTRY/$image:$version"

    if gcloud artifacts docker images describe "$full_image" &>/dev/null; then
        return 0
    else
        return 1
    fi
}

#######################################
# Build and push a Docker image
# Arguments:
#   $1 - directory containing Dockerfile
#   $2 - image name
#   $3 - version/tag
#   $4 - (optional) also tag as :latest
# Returns:
#   0 on success, 1 on failure
#######################################
build_and_push() {
    local dir="$1"
    local image="$2"
    local version="$3"
    local tag_latest="${4:-false}"
    local full_image="$REGISTRY/$image:$version"

    cd "$dir"

    if [[ "$tag_latest" == "true" ]]; then
        docker buildx build \
            --pull \
            --no-cache \
            --build-arg BUILDKIT_INLINE_CACHE=1 \
            --platform linux/arm64,linux/amd64 \
            --tag "$full_image" \
            --tag "$REGISTRY/$image:latest" \
            --push .
    else
        docker buildx build \
            --pull \
            --no-cache \
            --build-arg BUILDKIT_INLINE_CACHE=1 \
            --platform linux/arm64,linux/amd64 \
            --tag "$full_image" \
            --push .
    fi

    return $?
}

#######################################
# Process base image in dry-run mode
#######################################
process_base_image_dry_run() {
    local dir="$SCRIPT_DIR/$BASE_IMAGE_DIR"
    local config_file="$dir/auto-build-config.yaml"

    if ! parse_config "$config_file"; then
        return 1
    fi

    local full_image="$REGISTRY/$IMAGE:$VERSION"

    printf "%-40s %-35s " "$BASE_IMAGE_DIR" "$IMAGE:$VERSION"

    if image_exists "$IMAGE" "$VERSION"; then
        echo "[EXISTS]"
        SKIPPED_IMAGES+=("$full_image")
    else
        echo "[PENDING]"
        PENDING_IMAGES+=("$full_image")
    fi

    return 0
}

#######################################
# Process base image (build)
#######################################
process_base_image() {
    local dir="$SCRIPT_DIR/$BASE_IMAGE_DIR"
    local config_file="$dir/auto-build-config.yaml"

    if ! parse_config "$config_file"; then
        return 1
    fi

    local full_image="$REGISTRY/$IMAGE:$VERSION"

    echo "=========================================="
    echo "Building Base Image"
    echo "=========================================="
    echo "Directory: $BASE_IMAGE_DIR"
    echo "Image:     $IMAGE:$VERSION (+ :latest)"
    echo "----------------------------------------"

    if [[ "$FORCE_BUILD" != true ]] && image_exists "$IMAGE" "$VERSION"; then
        echo "SKIPPED: Base image already exists in registry"
        SKIPPED_IMAGES+=("$full_image")
        echo ""
        return 0
    fi

    if [[ "$FORCE_BUILD" == true ]] && image_exists "$IMAGE" "$VERSION"; then
        echo "FORCE: Rebuilding existing base image..."
    fi

    echo "Building and pushing (with :latest tag)..."
    if build_and_push "$dir" "$IMAGE" "$VERSION" "true"; then
        echo "SUCCESS: Built and pushed $full_image"
        BUILT_IMAGES+=("$full_image")
    else
        echo "FAILED: Could not build base image"
        return 1
    fi

    echo ""
    return 0
}

#######################################
# Find all server directories containing auto-build-config.yaml
# Returns:
#   Prints directory paths to stdout
#######################################
find_server_dirs() {
    find "$SCRIPT_DIR/$SERVERS_BASE_DIR" -name "auto-build-config.yaml" -exec dirname {} \; | sort
}

#######################################
# Print summary for dry-run mode
#######################################
print_dry_run_summary() {
    echo ""
    echo "=========================================="
    echo "Dry Run Summary"
    echo "=========================================="

    if [[ ${#SKIPPED_IMAGES[@]} -gt 0 ]]; then
        echo ""
        echo "Already exist in registry (${#SKIPPED_IMAGES[@]}):"
        for img in "${SKIPPED_IMAGES[@]}"; do
            echo "  [EXISTS] $img"
        done
    fi

    if [[ ${#PENDING_IMAGES[@]} -gt 0 ]]; then
        echo ""
        echo "Need to be built (${#PENDING_IMAGES[@]}):"
        for img in "${PENDING_IMAGES[@]}"; do
            echo "  [PENDING] $img"
        done
    fi

    echo ""
    echo "Run without --dry-run to build pending images"
}

#######################################
# Print build summary
#######################################
print_summary() {
    echo ""
    echo "=========================================="
    echo "Build Summary"
    echo "=========================================="

    if [[ ${#BUILT_IMAGES[@]} -gt 0 ]]; then
        echo ""
        echo "Built and pushed (${#BUILT_IMAGES[@]}):"
        for img in "${BUILT_IMAGES[@]}"; do
            echo "  - $img"
        done
    fi

    if [[ ${#SKIPPED_IMAGES[@]} -gt 0 ]]; then
        echo ""
        echo "Skipped (already exist) (${#SKIPPED_IMAGES[@]}):"
        for img in "${SKIPPED_IMAGES[@]}"; do
            echo "  - $img"
        done
    fi
}

#######################################
# Process a single server directory in dry-run mode
# Arguments:
#   $1 - server directory path
#######################################
process_server_dry_run() {
    local dir="$1"
    local config_file="$dir/auto-build-config.yaml"
    local relative_dir="${dir#$SCRIPT_DIR/}"

    if ! parse_config "$config_file"; then
        return 1
    fi

    local full_image="$REGISTRY/$IMAGE:$VERSION"

    printf "%-40s %-35s " "$relative_dir" "$IMAGE:$VERSION"

    if image_exists "$IMAGE" "$VERSION"; then
        echo "[EXISTS]"
        SKIPPED_IMAGES+=("$full_image")
    else
        echo "[PENDING]"
        PENDING_IMAGES+=("$full_image")
    fi

    return 0
}

#######################################
# Process a single server directory
# Arguments:
#   $1 - server directory path
# Returns:
#   0 on success, 1 on failure
#######################################
process_server() {
    local dir="$1"
    local config_file="$dir/auto-build-config.yaml"
    local relative_dir="${dir#$SCRIPT_DIR/}"

    if ! parse_config "$config_file"; then
        return 1
    fi

    local full_image="$REGISTRY/$IMAGE:$VERSION"

    echo "----------------------------------------"
    echo "Server: $relative_dir"
    echo "Image:  $IMAGE:$VERSION"
    echo "----------------------------------------"

    if [[ "$FORCE_BUILD" != true ]] && image_exists "$IMAGE" "$VERSION"; then
        echo "SKIPPED: Image already exists in registry"
        SKIPPED_IMAGES+=("$full_image")
        echo ""
        return 0
    fi

    if [[ "$FORCE_BUILD" == true ]] && image_exists "$IMAGE" "$VERSION"; then
        echo "FORCE: Rebuilding existing image..."
    fi

    echo "Building and pushing..."
    if build_and_push "$dir" "$IMAGE" "$VERSION"; then
        echo "SUCCESS: Built and pushed $full_image"
        BUILT_IMAGES+=("$full_image")
    else
        echo "FAILED: Could not build $full_image"
        return 1
    fi

    echo ""
    return 0
}

#######################################
# Main entry point
#######################################
main() {
    parse_args "$@"

    local mode_label="Building"
    if [[ "$DRY_RUN" == true ]]; then
        mode_label="Checking"
    fi

    echo "=========================================="
    echo "$mode_label all MCP servers"
    echo "Registry: $REGISTRY"
    if [[ "$DRY_RUN" == true ]]; then
        echo "Mode: DRY RUN (no builds)"
    elif [[ "$FORCE_BUILD" == true ]]; then
        echo "Mode: FORCE (rebuild all)"
    fi
    echo "=========================================="
    echo ""

    local server_dirs=()
    while IFS= read -r dir; do
        server_dirs+=("$dir")
    done < <(find_server_dirs)

    if [[ ${#server_dirs[@]} -eq 0 ]]; then
        echo "No servers found with auto-build-config.yaml"
        exit 1
    fi

    echo "Found 1 base image + ${#server_dirs[@]} server(s)"
    echo ""

    if [[ "$DRY_RUN" == true ]]; then
        printf "%-40s %-35s %s\n" "DIRECTORY" "IMAGE:VERSION" "STATUS"
        printf "%-40s %-35s %s\n" "---------" "-------------" "------"

        # Check base image first
        process_base_image_dry_run

        # Then check all servers
        for dir in "${server_dirs[@]}"; do
            process_server_dry_run "$dir"
        done

        print_dry_run_summary
    else
        # Build base image first
        process_base_image

        echo "=========================================="
        echo "Building MCP Servers"
        echo "=========================================="
        echo ""

        # Then build all servers
        for dir in "${server_dirs[@]}"; do
            process_server "$dir"
        done

        print_summary
    fi
}

# Run main
main "$@"
