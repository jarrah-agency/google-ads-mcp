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
| `SERVER_URL` | Yes | Deployed base URL, e.g. `https://jarrah-gads-mcp.fly.dev` — no trailing slash |
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

### MCP endpoint path

FastMCP mounts the streamable-HTTP MCP endpoint at `/mcp`, not `/`. Claude Desktop and other
clients that POST to the root URL are handled by `_McpRootRewrite` in `server.py`, an ASGI
middleware that rewrites `scope["path"]` from `/` to `/mcp` before the request reaches the app.

`resource_server_url` in `AuthSettings` is set to `f"{SERVER_URL}/mcp"` so the
`/.well-known/oauth-protected-resource` metadata advertises the correct endpoint.

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
| `generate_keyword_ideas` | Keyword Planner: discover new keywords from seeds/URL with volume, competition, bid estimates |
| `get_keyword_historical_metrics` | Keyword Planner: fetch metrics for a known list of keywords |

### Keyword Planner notes

- Uses `KeywordPlanIdeaService.GenerateKeywordIdeas` — **not** `search` or `search_stream`.
- Geo targeting uses a hardcoded `_COUNTRY_GEO_ID` dict in `google_ads.py` (ISO 3166-1 alpha-2 →
  criteria ID, e.g. `AU=2036`, `US=2840`). Do **not** use `SuggestGeoTargetConstants` — it requires
  `location_names` or `geo_targets` in the request body and errors if only `country_code` is set.
- Seed objects (`KeywordSeed`, `UrlSeed`, `KeywordAndUrlSeed`) use proto-plus and do **not** have
  `CopyFrom()`. Set fields directly on `request.keyword_seed`, `request.url_seed`, etc.
- `KeywordPlanIdeaService` has tight daily quotas on Test/Basic developer token access levels.
  Apply for Standard access in the Google Ads API Center for production use.

## Deploying to Fly.io

The app is deployed at `https://jarrah-gads-mcp.fly.dev` (region: `syd`, 512 MB RAM, persistent
volume at `/data`).

```bash
fly deploy                          # build and deploy
fly logs --app jarrah-gads-mcp      # stream logs
fly machine restart <id>            # restart a crashed machine
fly machine update <id> --vm-memory 512 --yes   # resize RAM
fly secrets set KEY=value           # set env vars (triggers redeploy)
```

To set up from scratch:
1. `fly launch` — creates app and `fly.toml`
2. `fly volumes create gads_mcp_data --region syd --size 1`
3. `fly secrets set GOOGLE_ADS_DEVELOPER_TOKEN=... GOOGLE_ADS_CLIENT_ID=... GOOGLE_ADS_CLIENT_SECRET=... SERVER_URL=https://<app>.fly.dev FERNET_KEY=... DB_PATH=/data/gads_mcp.db`
4. In Google Cloud Console: add `https://<app>.fly.dev/oauth/google/callback` as an authorized redirect URI on the **Web application** OAuth client.

## PyPI package

To release a new version:
1. Bump `version` in `pyproject.toml`
2. `python3 -m build`
3. `twine upload dist/* -u __token__ -p <pypi_token>`

## GAQL notes

- `run_gaql` uses `ga_service.search()` (not `search_stream`) — suitable for ad-hoc queries up to 100 rows.
- Always include a date filter; Google Ads API rejects most queries without one.
- Customer ID dashes are stripped inside `run_gaql` before the API call.
- The `_compact()` helper in `google_ads_tools.py` recursively drops keys with empty dict, empty list,
  `None`, `0`, `""`, or `False` values to keep LLM context concise.
- `mcp` is pinned to `<2` — the 2.x release renamed `FastMCP` and broke the server.

## Known issues / gotchas

- `db.py` uses `threading.RLock()` (not `Lock()`) — `init_db()` acquires the lock then calls
  `_get_conn()` which also acquires it; a non-reentrant lock would deadlock on startup.
- The Fly.io free tier machine (256 MB) is too small — the Google Ads gRPC library spikes memory
  during tool calls. Run at 512 MB minimum.
- Health check timeout is 5s; long-running Google Ads API calls can cause a single check to fail.
  This is harmless unless the machine is genuinely OOM-crashing.
