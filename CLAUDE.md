# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A remote HTTP MCP server exposing Google Ads tools. Users authenticate with their own Google accounts
via OAuth 2.0, select a Google Ads account, and the server issues MCP bearer tokens that carry their
credentials. Published to PyPI as `jarrah-gads-mcp`.

Derived from the Parabola AI app (`jarrah-agency/parabola-ai`) — `google_ads.py` and `google_ads_tools.py`
are taken from that codebase.

## Running locally for development

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in SERVER_URL, GOOGLE_ADS_* credentials, FERNET_KEY
SERVER_URL=http://localhost:8000 python3 -m jarrah_gads_mcp.server
```

## Environment variables

| Variable | Required | Notes |
|---|---|---|
| `GOOGLE_ADS_DEVELOPER_TOKEN` | Yes | From Google Ads API Centre |
| `GOOGLE_ADS_CLIENT_ID` | Yes | **Web application** OAuth client in Google Cloud Console |
| `GOOGLE_ADS_CLIENT_SECRET` | Yes | Same Web application OAuth client |
| `SERVER_URL` | Yes | Deployed base URL, e.g. `https://my.up.railway.app` — no trailing slash |
| `FERNET_KEY` | Recommended | Base64 Fernet key for encrypting Google refresh tokens in SQLite. Auto-generates one if absent (tokens lost on restart). Generate: `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `PORT` | No | HTTP port (default 8000) |
| `DB_PATH` | No | Path to SQLite DB (default: `gads_mcp.db` beside project root) |

`GOOGLE_ADS_REFRESH_TOKEN`, `GOOGLE_ADS_CUSTOMER_ID`, and `GOOGLE_ADS_LOGIN_CUSTOMER_ID` are **no longer
needed** — credentials are obtained per-user via the OAuth flow.

## Architecture

```
jarrah_gads_mcp/
  db.py            — SQLite CRUD (sync functions, called via asyncio.to_thread)
  auth_provider.py — GoogleAdsOAuthProvider implementing OAuthAuthorizationServerProvider
  server.py        — FastMCP server with auth, tools, and custom routes
  google_ads.py    — GoogleAdsClient wrapper + fetch_* functions returning DataFrames
  google_ads_tools.py — run_gaql() async wrapper with JSON output
```

Root-level `server.py` is the original stdio version kept for reference; `jarrah_gads_mcp/server.py`
is the authoritative remote version used by the PyPI entry point.

### OAuth flow

```
MCP client → GET /authorize
  → authorize() stores pending state → redirects to Google OAuth
Google → GET /oauth/google/callback
  → exchanges Google code for Google refresh token
  → lists accessible Google Ads accounts
  → single account: auto-selects; multiple: shows HTML account picker
  → creates MCP auth code → redirects back to MCP client redirect_uri
MCP client → POST /token
  → exchange_authorization_code() mints MCP access+refresh tokens
  → tokens carry (encrypted Google refresh token, customer_id, login_customer_id)
MCP tool call → bearer token verified via load_access_token()
  → GadsAccessToken contains decrypted google_refresh_token
  → tools call _creds() → get_access_token() to retrieve credentials
```

### Key types

- `GadsAccessToken(AccessToken)` — extends with `google_refresh_token`, `customer_id`, `login_customer_id`
- `GadsAuthCode(AuthorizationCode)` — extends with `google_rt_enc`, `customer_id`, `login_customer_id`
- `GadsRefreshToken(RefreshToken)` — extends with `google_rt_enc`, `customer_id`, `login_customer_id`

Note from provider.py: "FastMCP doesn't render any of these types in the user response, so it's OK to
add fields to subclasses which should not be exposed externally."

### Access token context in tools

Tools retrieve per-user credentials via:
```python
from mcp.server.auth.middleware.auth_context import get_access_token
token = get_access_token()  # returns GadsAccessToken for authenticated requests
```

This works because `AuthContextMiddleware` stores the authenticated user in a contextvar.

### Custom routes

`@mcp.custom_route()` adds routes that bypass MCP auth middleware:
- `GET /oauth/google/callback` — receives Google OAuth callback
- `POST /oauth/select-account` — handles account picker form
- `GET /health` — health check

### Tools

| Tool | Function |
|---|---|
| `run_gaql` | Execute arbitrary GAQL against the authenticated account |
| `list_accounts` | List all accounts accessible with the authenticated user's Google token |
| `list_client_accounts` | List leaf accounts under an MCC manager |
| `get_campaign_metrics` | Spend/impressions/clicks/IS by date, device, channel |
| `get_conversion_actions` | Enabled conversions with 90-day volume |
| `get_quality_scores` | Keyword QS (1–10) with ad relevance, landing page, expected CTR |
| `get_ad_creative` | RSA headlines, descriptions, final URLs for all active ads |

## Deploying to Railway

1. Create a Railway project, link to this GitHub repo
2. Add a persistent volume mounted at `/data`, set `DB_PATH=/data/gads_mcp.db`
3. Set all required env vars in Railway dashboard
4. Add `SERVER_URL=https://<your-app>.up.railway.app`
5. In Google Cloud Console: add `https://<your-app>.up.railway.app/oauth/google/callback`
   as an authorized redirect URI for your **Web application** OAuth client

Start command: `jarrah-gads-mcp` (PyPI entry point) or `python3 -m jarrah_gads_mcp.server`.

## PyPI package

To release a new version:
1. Bump `version` in `pyproject.toml`
2. `python3 -m build`
3. `twine upload dist/* -u __token__ -p <pypi_token>`

## TODO

1. **Deploy to Railway** — create Railway project, link repo, add persistent volume at `/data`, set all env vars (see Deploying to Railway section above).
2. **New Google Cloud OAuth app** — create a **Web application** type OAuth client (not Desktop), add the Railway callback URL as an authorized redirect URI: `https://<your-app>.up.railway.app/oauth/google/callback`.
3. **Generate FERNET_KEY** — `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` — store in Railway env vars.
4. **Connect in Claude Desktop** — add the MCP server URL (`https://<your-app>.up.railway.app`) as a remote MCP connector; Claude will trigger the OAuth flow on first use.
5. **Publish updated PyPI package** — bump version in `pyproject.toml`, build and upload with `twine` so users can install the remote version via `uvx jarrah-gads-mcp`.

## GAQL notes

- `run_gaql` uses `ga_service.search()` (not `search_stream`) — suitable for ad-hoc queries up to 100 rows.
- Always include a date filter; Google Ads API rejects most queries without one.
- Customer ID dashes are stripped inside `run_gaql` before the API call.
- The `_compact()` helper in `google_ads_tools.py` recursively drops keys with empty dict, empty list,
  `None`, `0`, `""`, or `False` values to keep LLM context concise.
- `mcp` is pinned to `<2` — the 2.x release renamed `FastMCP` and broke the server.
