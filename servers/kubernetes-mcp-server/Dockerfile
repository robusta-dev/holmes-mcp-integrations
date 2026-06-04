# Patches UBI 9 CVEs in upstream containers/kubernetes-mcp-server v0.0.61-oauth:
#   - libcap   CVE-2026-4878
#   - krb5-libs CVE-2026-40356
FROM us-central1-docker.pkg.dev/genuine-flight-317411/mcp/kubernetes-mcp-server:v0.0.61-oauth

USER 0

RUN microdnf upgrade -y libcap krb5-libs \
    && microdnf clean all \
    && rm -rf /var/cache/yum /var/cache/dnf

USER 65532
