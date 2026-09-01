# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A minimal MCP server that exposes Google Ads query capabilities as tools. It was extracted from the Parabola AI app (`jarrah-agency/parabola-ai`) — `google_ads.py` and `google_ads_tools.py` are derived from that codebase.

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

Three files, no framework beyond `mcp` and `google-ads`:

- **`server.py`** — FastMCP server. Reads credentials from env on startup. Exposes three tools: `run_gaql`, `list_accounts`, `list_client_accounts`. Imports from the other two files lazily (inside tool functions) to keep startup fast.
- **`google_ads.py`** — `build_client()` constructs a `GoogleAdsClient` from env vars + a refresh token. Also contains `list_accessible_customers()`, `list_client_accounts()`, and several `fetch_*` functions (campaign metrics, quality scores, conversions, ads) that return pandas DataFrames — these are not currently wired up as MCP tools but are available to add.
- **`google_ads_tools.py`** — `run_gaql(query, customer_id, refresh_token, login_customer_id)` executes arbitrary GAQL via `ga_service.search()`, compacts the proto-plus rows to plain dicts (dropping empty/null/zero fields), caps at 100 rows, and returns a JSON string. Errors from `GoogleAdsException` are returned as readable strings rather than raised, so the LLM can see and react to them.

## Registering with Claude Code

Add to `~/.claude/claude_desktop_config.json` (or equivalent MCP config):

```json
{
  "mcpServers": {
    "google-ads": {
      "command": "python",
      "args": ["/path/to/google-ads-mcp/server.py"]
    }
  }
}
```

## Adding new tools

The `fetch_*` functions in `google_ads.py` (campaign metrics, quality scores, conversion actions, ad creative) are ready to wrap as tools. Each takes `(refresh_token, customer_id, login_customer_id, ...)` — credentials come from the module-level env vars, so new tool functions in `server.py` just call them directly without passing credentials.

## GAQL notes

- `run_gaql` uses `ga_service.search()` (not `search_stream`) — suitable for ad-hoc queries up to 100 rows.
- Always include a date filter; Google Ads API rejects most queries without one.
- Customer ID dashes are stripped inside `run_gaql` before the API call.
- The `_compact()` helper in `google_ads_tools.py` recursively drops keys with empty dict, empty list, `None`, `0`, `""`, or `False` values to keep LLM context concise.
