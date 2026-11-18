# Sunset: legacy AWS MCP images

The `aws-api-mcp-server` image (this directory) and the `multi-aws-api-mcp-server` image (`../aws-multi-account`) are no longer supported. They get no further CVE patches or fixes, and the Holmes chart will stop deploying them in an upcoming release. Both wrap `awslabs.aws-api-mcp-server`, which [AWS has deprecated](https://github.com/awslabs/mcp/blob/main/src/aws-api-mcp-server/MIGRATION.md).

Use the hosted [AWS MCP Server](https://docs.aws.amazon.com/agent-toolkit/latest/userguide/mcp-server.html) instead. Holmes signs each request with its own AWS credentials, so no MCP pod is deployed.

## Migrate

1. Export your current values:

   ```bash
   helm get values RELEASE -n NAMESPACE > values.yaml
   ```

2. Point the IAM role's trust policy at the **Holmes service account** (`system:serviceaccount:NAMESPACE:<release>-holmes-service-account`, or `robusta-holmes-service-account` for the Robusta chart) instead of `aws-api-mcp-sa`. `setup-irsa.sh` creates this trust when you pass `--namespace NAMESPACE --service-account <that name>`, and `../aws-multi-account/scripts/setup-multi-account-iam.sh` does when each cluster's `namespace` and `service_account` in its config name the Holmes service account.
3. Set `mcpAddons.aws.hosted.enabled: true` in `values.yaml`, and for a single account add the role to Holmes' service account (`serviceAccount.annotations: eks.amazonaws.com/role-arn`). Multi-account `profiles` keep working unchanged. For the Robusta chart, nest these values under `holmes:`.
4. Upgrade:

   ```bash
   helm upgrade RELEASE CHART -n NAMESPACE -f values.yaml
   ```

In hosted mode `mcpAddons.aws.serviceAccount`, `image`, `networkPolicy` and `resources` are ignored. Full setup: [Holmes AWS docs](https://holmesgpt.dev/data-sources/builtin-toolsets/aws/).
