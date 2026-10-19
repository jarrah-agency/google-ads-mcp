# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A minimal MCP server that exposes Google Ads query capabilities as tools. It was extracted from the Parabola AI app (`jarrah-agency/parabola-ai`) — `google_ads.py` and `google_ads_tools.py` are derived from that codebase.

Published to PyPI as `jarrah-gads-mcp`.

## Setup & running

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in credentials
python server.py       # runs on stdio transport
```

## Environment variables

| Variable | Required | Notes |
|---|---|---|
| `GOOGLE_ADS_DEVELOPER_TOKEN` | Yes | From Google Ads API Centre |
| `GOOGLE_ADS_CLIENT_ID` | Yes | Google Cloud Console OAuth app |
| `GOOGLE_ADS_CLIENT_SECRET` | Yes | Google Cloud Console OAuth app |
| `GOOGLE_ADS_REFRESH_TOKEN` | Yes | OAuth refresh token for the account to query |
| `GOOGLE_ADS_CUSTOMER_ID` | Yes | Account to query (digits only or with dashes) |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | No | MCC manager ID — only needed when querying through a manager account |

## Architecture

Source files exist in two locations:
- **Root** (`server.py`, `google_ads.py`, `google_ads_tools.py`) — used for local development via stdio
- **`jarrah_gads_mcp/`** — Python package for PyPI distribution; mirrors the root files with updated imports

The two sets of files must be kept in sync when making changes.

### Files

- **`server.py`** / **`jarrah_gads_mcp/server.py`** — FastMCP server. Reads credentials from env on startup. Exposes seven tools (see below). Imports from the other two files lazily (inside tool functions) to keep startup fast.
- **`google_ads.py`** / **`jarrah_gads_mcp/google_ads.py`** — `build_client()` constructs a `GoogleAdsClient` from env vars + a refresh token. Contains `list_accessible_customers()`, `list_client_accounts()`, and `fetch_*` functions that return pandas DataFrames.
- **`google_ads_tools.py`** / **`jarrah_gads_mcp/google_ads_tools.py`** — `run_gaql()` executes arbitrary GAQL via `ga_service.search()`, compacts proto-plus rows to plain dicts, caps at 100 rows, returns JSON string. `GoogleAdsException` errors are returned as strings rather than raised.

### Tools

| Tool | Function |
|---|---|
| `run_gaql` | Execute arbitrary GAQL against the configured account |
| `list_accounts` | List all accounts accessible with the refresh token |
| `list_client_accounts` | List leaf accounts under an MCC manager |
| `get_campaign_metrics` | Spend/impressions/clicks/IS by date, device, channel |
| `get_conversion_actions` | Enabled conversions with 90-day volume |
| `get_quality_scores` | Keyword QS (1–10) with ad relevance, landing page, expected CTR |
| `get_ad_creative` | RSA headlines, descriptions, final URLs for all active ads |

## PyPI package

Published as `jarrah-gads-mcp`. To release a new version:
1. Bump `version` in `pyproject.toml`
2. `python -m build`
3. `twine upload dist/* -u __token__ -p <pypi_token>`

## Registering with Claude Desktop (local)

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "google-ads": {
      "command": "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3",
      "args": ["/path/to/google-ads-mcp/server.py"],
      "env": {
        "GOOGLE_ADS_DEVELOPER_TOKEN": "...",
        "GOOGLE_ADS_CLIENT_ID": "...",
        "GOOGLE_ADS_CLIENT_SECRET": "...",
        "GOOGLE_ADS_REFRESH_TOKEN": "...",
        "GOOGLE_ADS_CUSTOMER_ID": "..."
      }
    }
  }
}
```

Use the full Python path — Claude Desktop does not inherit shell PATH reliably. `uvx` requires `uv` to be installed separately (`brew install uv`).

## TODO

- **Remote HTTP transport** — current stdio transport only works locally. To support team members and list on MCP directories properly, the server needs to be rewritten to use HTTP/SSE transport and deployed (Railway, Render, Fly.io). Each user would supply their own credentials via the config `env` block (Option A), or a full OAuth flow could be built (Option B).

## GAQL notes

- `run_gaql` uses `ga_service.search()` (not `search_stream`) — suitable for ad-hoc queries up to 100 rows.
- Always include a date filter; Google Ads API rejects most queries without one.
- Customer ID dashes are stripped inside `run_gaql` before the API call.
- The `_compact()` helper in `google_ads_tools.py` recursively drops keys with empty dict, empty list, `None`, `0`, `""`, or `False` values to keep LLM context concise.
- `mcp` is pinned to `<2` — the 2.x release renamed `FastMCP` and broke the server.
