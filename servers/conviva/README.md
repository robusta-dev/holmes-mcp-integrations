# Conviva MCP

Conviva provides streaming video (VSI) and digital performance (DPI) analytics through 5 hosted MCP servers. No in-cluster deployment is needed — Holmes connects directly to Conviva's hosted endpoints at `mcp.conviva.com`.

## Overview

This integration enables Holmes to interact with Conviva for:

- Querying aggregate streaming video quality metrics (bitrate, buffering, start failures, concurrent plays)
- Investigating AI-detected anomaly alerts for video streaming
- Looking up per-viewer session details for debugging individual experiences
- Monitoring web and app digital performance metrics (page load, API latency, error rates)
- Retrieving AI-detected anomaly alerts for digital performance

## Architecture

```
Holmes -> Conviva MCP (streamable-http) -> mcp.conviva.com
                                                |
                                    Hosted by Conviva (no deployment needed)
                                    (Auth via Basic header with API credential)
```

Unlike other MCP integrations in this repo, Conviva's MCP servers are fully hosted. There is nothing to deploy in your cluster — Holmes connects directly to `mcp.conviva.com` using streamable-http transport with Basic authentication.

## Quick Start

### 1. Get Your API Credential

1. Log in to [Conviva Pulse](https://pulse.conviva.com)
2. Go to **API Management** in your account settings
3. Generate or copy your API credential
4. The credential is used as-is in the `Authorization: Basic <credential>` header

### 2. Configure Holmes

Add the Conviva MCP servers to your Holmes configuration. You can include all 5 servers or only the ones relevant to your use case.

See [Holmes Integration](#holmes-integration) below for the full configuration.

## Holmes Integration

Add the MCP servers to your Holmes configuration. Each server is a separate endpoint — include only the ones you need.

### Direct Holmes Configuration

```yaml
mcp_servers:
  conviva_vsi_metrics:
    description: "Conviva VSI — real-time and historic aggregate streaming video metrics"
    config:
      url: "https://mcp.conviva.com/vsi/metrics"
      mode: streamable-http
      headers:
        Authorization: "Basic <CONVIVA_API_CREDENTIAL>"
    llm_instructions: |
      Use this to query aggregate streaming video quality metrics from Conviva.
      Covers metrics like concurrent plays, bitrate, video start failures,
      buffering ratio, and video startup time across dimensions like CDN,
      device, ISP, city, and content.

  conviva_vsi_ai_alerts:
    description: "Conviva VSI — AI-detected anomaly alerts for streaming video"
    config:
      url: "https://mcp.conviva.com/vsi/ai-alerts"
      mode: streamable-http
      headers:
        Authorization: "Basic <CONVIVA_API_CREDENTIAL>"
    llm_instructions: |
      Use this to retrieve AI-generated anomaly alerts for streaming video.
      These alerts detect sudden changes in video quality metrics like
      buffering, bitrate drops, or playback failures. Use when investigating
      streaming quality degradations or outages.

  conviva_vsi_sessions:
    description: "Conviva VSI — per-viewer session-level streaming details"
    config:
      url: "https://mcp.conviva.com/vsi/sessions"
      mode: streamable-http
      headers:
        Authorization: "Basic <CONVIVA_API_CREDENTIAL>"
    llm_instructions: |
      Use this to look up individual viewer session details from Conviva.
      Provides per-session data including device info, network conditions,
      content viewed, and quality events. Use when debugging a specific
      viewer's experience or correlating user-reported issues.

  conviva_dpi_metrics:
    description: "Conviva DPI — web and app digital performance metrics"
    config:
      url: "https://mcp.conviva.com/dpi/metrics"
      mode: streamable-http
      headers:
        Authorization: "Basic <CONVIVA_API_CREDENTIAL>"
    llm_instructions: |
      Use this to query digital performance metrics from Conviva.
      Covers web and app performance indicators like page load time,
      time to interactive, API latency, and error rates. Use when
      investigating frontend or app performance issues.

  conviva_dpi_ai_alerts:
    description: "Conviva DPI — AI-detected anomaly alerts for digital performance"
    config:
      url: "https://mcp.conviva.com/dpi/ai-alerts"
      mode: streamable-http
      headers:
        Authorization: "Basic <CONVIVA_API_CREDENTIAL>"
    llm_instructions: |
      Use this to retrieve AI-generated anomaly alerts for digital performance.
      These alerts detect sudden changes in web/app metrics like page load
      time spikes or error rate increases. Use when investigating digital
      experience degradations.
```

### Robusta Helm Chart

Create a Kubernetes Secret for the API credential:

```bash
kubectl create secret generic conviva-mcp-secret \
  --from-literal=CONVIVA_API_CREDENTIAL='<your-api-credential>'
```

Then add to your Robusta `generated_values.yaml`:

```yaml
holmes:
  additionalEnvVars:
    - name: CONVIVA_API_CREDENTIAL
      valueFrom:
        secretKeyRef:
          name: conviva-mcp-secret
          key: CONVIVA_API_CREDENTIAL

  custom_mcp_servers:
    conviva_vsi_metrics:
      description: "Conviva VSI — real-time and historic aggregate streaming video metrics"
      config:
        url: "https://mcp.conviva.com/vsi/metrics"
        mode: streamable-http
        headers:
          Authorization: "Basic {{ env.CONVIVA_API_CREDENTIAL }}"
      llm_instructions: |
        Use this to query aggregate streaming video quality metrics from Conviva.
        Covers metrics like concurrent plays, bitrate, video start failures,
        buffering ratio, and video startup time across dimensions like CDN,
        device, ISP, city, and content.

    conviva_vsi_ai_alerts:
      description: "Conviva VSI — AI-detected anomaly alerts for streaming video"
      config:
        url: "https://mcp.conviva.com/vsi/ai-alerts"
        mode: streamable-http
        headers:
          Authorization: "Basic {{ env.CONVIVA_API_CREDENTIAL }}"
      llm_instructions: |
        Use this to retrieve AI-generated anomaly alerts for streaming video.
        These alerts detect sudden changes in video quality metrics like
        buffering, bitrate drops, or playback failures. Use when investigating
        streaming quality degradations or outages.

    conviva_vsi_sessions:
      description: "Conviva VSI — per-viewer session-level streaming details"
      config:
        url: "https://mcp.conviva.com/vsi/sessions"
        mode: streamable-http
        headers:
          Authorization: "Basic {{ env.CONVIVA_API_CREDENTIAL }}"
      llm_instructions: |
        Use this to look up individual viewer session details from Conviva.
        Provides per-session data including device info, network conditions,
        content viewed, and quality events. Use when debugging a specific
        viewer's experience or correlating user-reported issues.

    conviva_dpi_metrics:
      description: "Conviva DPI — web and app digital performance metrics"
      config:
        url: "https://mcp.conviva.com/dpi/metrics"
        mode: streamable-http
        headers:
          Authorization: "Basic {{ env.CONVIVA_API_CREDENTIAL }}"
      llm_instructions: |
        Use this to query digital performance metrics from Conviva.
        Covers web and app performance indicators like page load time,
        time to interactive, API latency, and error rates. Use when
        investigating frontend or app performance issues.

    conviva_dpi_ai_alerts:
      description: "Conviva DPI — AI-detected anomaly alerts for digital performance"
      config:
        url: "https://mcp.conviva.com/dpi/ai-alerts"
        mode: streamable-http
        headers:
          Authorization: "Basic {{ env.CONVIVA_API_CREDENTIAL }}"
      llm_instructions: |
        Use this to retrieve AI-generated anomaly alerts for digital performance.
        These alerts detect sudden changes in web/app metrics like page load
        time spikes or error rate increases. Use when investigating digital
        experience degradations.
```

**Note:** You can omit any servers you don't need. For example, if you only monitor video streaming, remove the `conviva_dpi_*` entries. If you only monitor web/app performance, remove the `conviva_vsi_*` entries.

## Available Servers

Each Conviva MCP server exposes tools that are discovered at runtime. The table below describes each server's capabilities.

| Server | URL | Description |
|--------|-----|-------------|
| `conviva_vsi_metrics` | `https://mcp.conviva.com/vsi/metrics` | Aggregate streaming video quality metrics — concurrent plays, bitrate, buffering ratio, video start failures, startup time. Queryable across dimensions like CDN, device, ISP, city, and content. |
| `conviva_vsi_ai_alerts` | `https://mcp.conviva.com/vsi/ai-alerts` | AI-detected anomaly alerts for streaming video — automatic detection of sudden changes in video quality metrics. |
| `conviva_vsi_sessions` | `https://mcp.conviva.com/vsi/sessions` | Per-viewer session details — device info, network conditions, content viewed, quality events for individual viewing sessions. |
| `conviva_dpi_metrics` | `https://mcp.conviva.com/dpi/metrics` | Web and app digital performance metrics — page load time, time to interactive, API latency, error rates. |
| `conviva_dpi_ai_alerts` | `https://mcp.conviva.com/dpi/ai-alerts` | AI-detected anomaly alerts for digital performance — automatic detection of sudden changes in web/app metrics. |

## Security Considerations

1. **Credentials** — Store the API credential in a Kubernetes Secret, never in plain text in Helm values
2. **Credential Rotation** — Rotate the API credential periodically via Conviva Pulse and update the Kubernetes Secret
3. **Network Access** — Holmes pods need outbound HTTPS access to `mcp.conviva.com` (port 443)
4. **Minimal Scope** — Only include the MCP servers relevant to your use case

## Troubleshooting

### Authentication Errors

1. Verify your API credential is valid in Conviva Pulse > API Management
2. Ensure the `Authorization` header uses the format `Basic <credential>` (no extra encoding)
3. Check that the Kubernetes Secret is in the same namespace as Holmes

### Connection Issues

1. Verify Holmes pods can reach `mcp.conviva.com` over HTTPS:
   ```bash
   kubectl exec -it <holmes-pod> -- curl -s -o /dev/null -w "%{http_code}" https://mcp.conviva.com/vsi/metrics
   ```
2. Check if a network policy or firewall is blocking outbound HTTPS to `mcp.conviva.com`

### Tools Not Appearing

1. Tools are discovered at runtime when Holmes connects — verify the connection succeeds first
2. Check Holmes logs for MCP connection errors:
   ```bash
   kubectl logs -l app=holmes | grep -i conviva
   ```

## File Structure

```
conviva/
├── holmes-config/
│   └── conviva-toolset.yaml    # Holmes MCP configuration for all 5 servers
└── README.md                   # This file
```

## References

- [Conviva MCP Documentation](https://docs.conviva.com/learning-center-files/content/api_developer_center/ssd/conviva_connect/conviva_connect_mcp.htm)
- [Conviva Pulse](https://pulse.conviva.com)
