# googleads-mcp

A minimal MCP server that exposes Google Ads query capabilities as tools for use with Claude and other MCP clients.

## Tools

| Tool | Description |
|---|---|
| `run_gaql` | Execute any GAQL query against the configured account |
| `list_accounts` | List all accounts accessible with the refresh token |
| `list_client_accounts` | List leaf accounts under an MCC manager |
| `get_campaign_metrics` | Campaign spend, impressions, clicks, and impression share by date/device/channel |
| `get_conversion_actions` | Enabled conversions with 90-day volume |
| `get_quality_scores` | Keyword quality scores with ad relevance, landing page, and CTR components |
| `get_ad_creative` | RSA headlines, descriptions, and final URLs for all active ads |

## Setup

### 1. Install

```bash
pip install googleads-mcp
```

### 2. Configure credentials

Create a `.env` file in your working directory:

```env
GOOGLE_ADS_DEVELOPER_TOKEN=your_developer_token
GOOGLE_ADS_CLIENT_ID=your_client_id
GOOGLE_ADS_CLIENT_SECRET=your_client_secret
GOOGLE_ADS_REFRESH_TOKEN=your_refresh_token
GOOGLE_ADS_CUSTOMER_ID=123-456-7890
# GOOGLE_ADS_LOGIN_CUSTOMER_ID=  # only needed for MCC manager accounts
```

| Variable | Required | Notes |
|---|---|---|
| `GOOGLE_ADS_DEVELOPER_TOKEN` | Yes | From Google Ads API Centre |
| `GOOGLE_ADS_CLIENT_ID` | Yes | Google Cloud Console OAuth app |
| `GOOGLE_ADS_CLIENT_SECRET` | Yes | Google Cloud Console OAuth app |
| `GOOGLE_ADS_REFRESH_TOKEN` | Yes | OAuth refresh token for the account to query |
| `GOOGLE_ADS_CUSTOMER_ID` | Yes | Account to query (digits only or with dashes) |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | No | MCC manager ID — only needed when querying through a manager account |

### 3. Add to Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "google-ads": {
      "command": "uvx",
      "args": ["googleads-mcp"]
    }
  }
}
```

Restart Claude Desktop. The tools will appear in the tools panel.

## GAQL notes

- `run_gaql` uses `search()` (not `search_stream`) — suitable for ad-hoc queries up to 100 rows.
- Always include a date filter; the Google Ads API rejects most queries without one.
- Customer ID dashes are stripped automatically before the API call.

## Source

[github.com/jarrah-agency/google-ads-mcp](https://github.com/jarrah-agency/google-ads-mcp)
