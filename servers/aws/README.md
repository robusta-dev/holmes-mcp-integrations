# AWS MCP Server IAM setup for Holmes

Holmes talks directly to the hosted [AWS MCP Server](https://docs.aws.amazon.com/agent-toolkit/latest/userguide/mcp-server.html) (`https://aws-mcp.<region>.api.aws/mcp`) and signs every request with its own AWS credentials, so **no image is built here any more**. The deprecated `aws-api-mcp-server` and `multi-aws-api-mcp-server` images that used to live in this directory are gone; this directory only keeps the IAM helper scripts referenced from the [Holmes AWS docs](https://holmesgpt.dev/data-sources/builtin-toolsets/aws/).

## Architecture

```
Holmes (SigV4 with IRSA / profiles) → https://aws-mcp.<region>.api.aws/mcp → AWS APIs
```

## Files

- **`aws-mcp-iam-policy.json`** - Read-only IAM policy for the services Holmes may query. Read-only access is enforced by this policy; the hosted server itself can run any operation the credentials allow.
- **`enable-oidc-provider.sh`** - Associates the EKS cluster's OIDC provider with IAM (prerequisite for IRSA).
- **`setup-irsa.sh`** - Single account: creates the policy and an IAM role whose trust policy names the **Holmes service account**, then prints the Helm values to apply.
  ```bash
  ./setup-irsa.sh --cluster-name my-cluster --region us-east-1 --namespace holmes --service-account holmes-holmes-service-account
  ```
- **`scripts/setup-multi-account-iam.sh`** - Multi-account: creates OIDC providers and roles in each target account, trusting the Holmes service account of every listed cluster, and writes `holmes_config.yaml` with the `mcpAddons.aws.multiAccount` values.
  ```bash
  ./scripts/setup-multi-account-iam.sh setup my-config.yaml ./aws-mcp-iam-policy.json
  ./scripts/setup-multi-account-iam.sh verify my-config.yaml
  ./scripts/setup-multi-account-iam.sh teardown my-config.yaml
  ```
  Requires `aws`, `jq` and `yq`; see `scripts/multi-cluster-config-example.yaml` for the config format.

The Holmes service account is `<release>-holmes-service-account` for the Holmes chart and `robusta-holmes-service-account` for the Robusta chart.

## Verify

```bash
# Holmes must see the IAM role, not the node role
kubectl exec -n <namespace> deploy/holmes-holmes -- python -c "import boto3; print(boto3.client('sts').get_caller_identity()['Arn'])"
```
