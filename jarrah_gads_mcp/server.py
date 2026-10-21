"""Google Ads MCP server — remote HTTP transport with OAuth 2.0.

Users authenticate with their own Google accounts via OAuth, select a Google Ads
account, and the server issues MCP access tokens that carry encrypted Google Ads
credentials. All tools read credentials from the per-request access token context.

Required environment variables:
  GOOGLE_ADS_DEVELOPER_TOKEN  — from Google Ads API Centre
  GOOGLE_ADS_CLIENT_ID        — Google Cloud Console OAuth Web app client ID
  GOOGLE_ADS_CLIENT_SECRET    — Google Cloud Console OAuth Web app client secret
  SERVER_URL                  — public base URL of this server (e.g. https://my.railway.app)

Optional:
  FERNET_KEY                  — base64 Fernet key for encrypting refresh tokens at rest
                                (auto-generated if absent, but tokens won't survive restarts)
  PORT                        — HTTP port (default 8000)
  DB_PATH                     — path to SQLite DB file (default: gads_mcp.db beside package)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from datetime import date
from typing import Any
from urllib.parse import urlencode

import httpx
from dotenv import load_dotenv
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from jarrah_gads_mcp import db as _db
from jarrah_gads_mcp.auth_provider import (
    GadsAccessToken,
    GoogleAdsOAuthProvider,
    decrypt,
    encrypt,
)

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

SERVER_URL = os.environ.get("SERVER_URL", "http://localhost:8000").rstrip("/")
PORT = int(os.environ.get("PORT", "8000"))

# ---------------------------------------------------------------------------
# Initialise DB and build FastMCP server
# ---------------------------------------------------------------------------

_db.init_db()

provider = GoogleAdsOAuthProvider(server_url=SERVER_URL)

mcp = FastMCP(
    "google-ads",
    auth_server_provider=provider,
    auth=AuthSettings(
        issuer_url=SERVER_URL,  # type: ignore[arg-type]
        resource_server_url=SERVER_URL,  # type: ignore[arg-type]
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=["google-ads"],
            default_scopes=["google-ads"],
        ),
        revocation_options=RevocationOptions(enabled=True),
        required_scopes=["google-ads"],
    ),
    host="0.0.0.0",
    port=PORT,
)


# ---------------------------------------------------------------------------
# Helper: get current user's Google Ads credentials from bearer token
# ---------------------------------------------------------------------------

def _creds() -> tuple[str, str, str | None]:
    """Return (google_refresh_token, customer_id, login_customer_id) for the current request."""
    token = get_access_token()
    if not isinstance(token, GadsAccessToken):
        raise RuntimeError("Not authenticated or missing credentials")
    return token.google_refresh_token, token.customer_id, token.login_customer_id


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def run_gaql(query: str) -> str:
    """Execute a GAQL (Google Ads Query Language) query against the authenticated account.

    Always include a date filter such as:
      segments.date DURING LAST_30_DAYS
      segments.date BETWEEN 'YYYY-MM-DD' AND 'YYYY-MM-DD'

    Limit results with LIMIT N (max 100).

    Common resources: campaign, ad_group, keyword_view, search_term_view,
    geographic_view, user_location_view, gender_view, age_range_view,
    audience_view, ad_group_ad, conversion_action.
    """
    refresh_token, customer_id, login_customer_id = _creds()
    from jarrah_gads_mcp.google_ads_tools import run_gaql as _run
    return await _run(query, customer_id, refresh_token, login_customer_id)


@mcp.tool()
async def list_accounts() -> list[dict]:
    """List all Google Ads accounts accessible with the authenticated user's credentials.

    Returns a list of {id, name, is_manager} dicts.
    Manager accounts (MCC) can be passed to list_client_accounts.
    """
    refresh_token, _cid, _lcd = _creds()

    def _sync():
        from jarrah_gads_mcp.google_ads import list_accessible_customers
        return list_accessible_customers(refresh_token)

    return await asyncio.to_thread(_sync)


@mcp.tool()
async def list_client_accounts(manager_id: str) -> list[dict]:
    """List leaf (non-manager) accounts under an MCC manager account.

    Args:
        manager_id: the customer ID of the MCC manager account.

    Returns a list of {id, name, status} dicts.
    """
    refresh_token, _cid, _lcd = _creds()

    def _sync():
        from jarrah_gads_mcp.google_ads import list_client_accounts as _list
        return _list(refresh_token, manager_id)

    return await asyncio.to_thread(_sync)


@mcp.tool()
async def get_campaign_metrics(start_date: str, end_date: str) -> list[dict]:
    """Get campaign performance metrics broken down by date, device, and channel type.

    Args:
        start_date: Start date in YYYY-MM-DD format.
        end_date: End date in YYYY-MM-DD format.

    Returns cost, impressions, clicks, and search impression share per campaign/date/device.
    """
    refresh_token, customer_id, login_customer_id = _creds()
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)

    def _sync():
        from jarrah_gads_mcp.google_ads import fetch_campaign_metrics
        df = fetch_campaign_metrics(refresh_token, customer_id, login_customer_id, start, end)
        records = df.to_dict(orient="records")
        for r in records:
            if hasattr(r.get("date"), "isoformat"):
                r["date"] = r["date"].isoformat()
        return records

    return await asyncio.to_thread(_sync)


@mcp.tool()
async def get_conversion_actions() -> list[dict]:
    """List enabled conversion actions with 90-day conversion volume.

    Returns name, type, category, and volume_90d for each conversion action,
    sorted by volume descending.
    """
    refresh_token, customer_id, login_customer_id = _creds()

    def _sync():
        from jarrah_gads_mcp.google_ads import list_conversion_actions
        return list_conversion_actions(refresh_token, customer_id, login_customer_id)

    return await asyncio.to_thread(_sync)


@mcp.tool()
async def get_quality_scores() -> list[dict]:
    """Get keyword quality scores with component breakdowns.

    Returns quality_score (1-10), ad_relevance, landing_page_experience, and
    expected_ctr for all active keywords. Component scores are ABOVE_AVERAGE,
    AVERAGE, or BELOW_AVERAGE.
    """
    refresh_token, customer_id, login_customer_id = _creds()

    def _sync():
        from jarrah_gads_mcp.google_ads import fetch_quality_scores
        df = fetch_quality_scores(refresh_token, customer_id, login_customer_id)
        return df.to_dict(orient="records")

    return await asyncio.to_thread(_sync)


@mcp.tool()
async def get_ad_creative() -> list[dict]:
    """Get ad creative for all active ads.

    Returns headlines, descriptions, and final URLs for responsive search ads.
    Other ad types return type and status only.
    """
    refresh_token, customer_id, login_customer_id = _creds()

    def _sync():
        from jarrah_gads_mcp.google_ads import fetch_ad_creative
        df = fetch_ad_creative(refresh_token, customer_id, login_customer_id)
        return df.to_dict(orient="records")

    return await asyncio.to_thread(_sync)


# ---------------------------------------------------------------------------
# Google OAuth callback — custom routes (bypass MCP auth)
# ---------------------------------------------------------------------------

@mcp.custom_route("/oauth/google/callback", methods=["GET"])
async def google_callback(request: Request) -> Response:
    """Receive Google OAuth callback, exchange code for tokens, redirect to account picker."""
    google_state = request.query_params.get("state")
    google_code = request.query_params.get("code")
    error = request.query_params.get("error")

    if error:
        return HTMLResponse(
            f"<h2>Google authorization failed</h2><p>{error}</p>",
            status_code=400,
        )

    if not google_state or not google_code:
        return HTMLResponse("<h2>Bad request</h2>", status_code=400)

    # Look up pending authorization
    pending = await asyncio.to_thread(_db.get_pending, google_state)
    if pending is None or pending["expires_at"] < int(time.time()):
        return HTMLResponse("<h2>Authorization expired or not found</h2>", status_code=400)

    # Exchange Google auth code for Google tokens
    google_redirect_uri = f"{SERVER_URL}/oauth/google/callback"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": google_code,
                "client_id": os.environ["GOOGLE_ADS_CLIENT_ID"],
                "client_secret": os.environ["GOOGLE_ADS_CLIENT_SECRET"],
                "redirect_uri": google_redirect_uri,
                "grant_type": "authorization_code",
            },
        )

    if resp.status_code != 200:
        log.error("Google token exchange failed: %s", resp.text)
        return HTMLResponse("<h2>Google token exchange failed</h2>", status_code=500)

    token_data = resp.json()
    google_refresh_token = token_data.get("refresh_token")
    if not google_refresh_token:
        return HTMLResponse(
            "<h2>Google did not return a refresh token.</h2>"
            "<p>Try revoking access at myaccount.google.com/permissions and retrying.</p>",
            status_code=400,
        )

    google_rt_enc = encrypt(google_refresh_token)

    # Store encrypted refresh token in the pending row for the account picker
    await asyncio.to_thread(_db.update_pending_google_rt, google_state, google_rt_enc)

    # List accessible Google Ads accounts
    accounts: list[dict] = []
    try:
        def _list_accounts():
            from jarrah_gads_mcp.google_ads import list_accessible_customers
            return list_accessible_customers(google_refresh_token)

        accounts = await asyncio.to_thread(_list_accounts)
    except Exception as exc:
        log.warning("Could not list accounts: %s", exc)

    if not accounts:
        # No accounts accessible — show error
        await asyncio.to_thread(_db.delete_pending, google_state)
        return HTMLResponse(
            "<h2>No Google Ads accounts found</h2>"
            "<p>The authenticated Google account does not have access to any Google Ads accounts.</p>",
            status_code=400,
        )

    if len(accounts) == 1:
        # Auto-select single account
        return await _finalize_account(
            google_state=google_state,
            pending=pending,
            google_rt_enc=google_rt_enc,
            accounts=accounts,
            selected_id=accounts[0]["id"],
        )

    # Multiple accounts — show picker
    return _render_picker(google_state, accounts)


@mcp.custom_route("/oauth/select-account", methods=["POST"])
async def select_account(request: Request) -> Response:
    """Handle account selection form POST."""
    form = await request.form()
    google_state = form.get("google_state", "")
    selected_id = form.get("customer_id", "")

    if not google_state or not selected_id:
        return HTMLResponse("<h2>Bad request</h2>", status_code=400)

    pending = await asyncio.to_thread(_db.get_pending, google_state)
    if pending is None or pending["expires_at"] < int(time.time()):
        return HTMLResponse("<h2>Authorization expired</h2>", status_code=400)
    if not pending.get("google_rt_enc"):
        return HTMLResponse("<h2>Session data missing</h2>", status_code=400)

    # Re-list accounts to validate the selected_id and get login_customer_id
    google_rt_enc = pending["google_rt_enc"]
    google_refresh_token = decrypt(google_rt_enc)
    accounts: list[dict] = []
    try:
        def _list():
            from jarrah_gads_mcp.google_ads import list_accessible_customers
            return list_accessible_customers(google_refresh_token)
        accounts = await asyncio.to_thread(_list)
    except Exception as exc:
        log.warning("Could not re-list accounts: %s", exc)

    return await _finalize_account(
        google_state=google_state,
        pending=pending,
        google_rt_enc=google_rt_enc,
        accounts=accounts,
        selected_id=selected_id,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _finalize_account(
    google_state: str,
    pending: dict,
    google_rt_enc: str,
    accounts: list[dict],
    selected_id: str,
) -> Response:
    """Create MCP auth code and redirect back to the MCP client."""
    # Determine login_customer_id (for MCC access)
    managers = [a for a in accounts if a.get("is_manager")]
    selected = next((a for a in accounts if a["id"] == selected_id), None)
    login_customer_id: str | None = None
    if selected and not selected.get("is_manager") and len(managers) == 1:
        login_customer_id = managers[0]["id"]

    mcp_code = secrets.token_urlsafe(32)
    expires_at = int(time.time()) + 300  # 5-minute code lifetime

    await asyncio.to_thread(
        _db.save_code,
        mcp_code,
        pending["client_id"],
        pending["redirect_uri"],
        True,  # redirect_uri_provided_explicitly
        pending["code_challenge"],
        pending["scopes"],
        google_rt_enc,
        selected_id,
        login_customer_id,
        None,  # resource
        expires_at,
    )

    await asyncio.to_thread(_db.delete_pending, google_state)

    # Build redirect back to MCP client
    redirect_uri = pending["redirect_uri"]
    params: dict[str, str] = {"code": mcp_code}
    if pending.get("mcp_state"):
        params["state"] = pending["mcp_state"]

    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(redirect_uri + sep + urlencode(params), status_code=302)


def _render_picker(google_state: str, accounts: list[dict]) -> HTMLResponse:
    rows = "\n".join(
        f"""<tr>
              <td><input type="radio" name="customer_id" value="{a['id']}" required
                  {'checked' if i == 0 else ''}></td>
              <td>{a.get('name') or '(unnamed)'}</td>
              <td>{a['id']}</td>
              <td>{'Manager' if a.get('is_manager') else 'Account'}</td>
            </tr>"""
        for i, a in enumerate(accounts)
    )
    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Select Google Ads Account</title>
  <style>
    body {{ font-family: sans-serif; max-width: 700px; margin: 60px auto; padding: 0 20px; }}
    h1 {{ font-size: 1.4rem; }}
    table {{ border-collapse: collapse; width: 100%; margin: 20px 0; }}
    th, td {{ text-align: left; padding: 8px 12px; border-bottom: 1px solid #ddd; }}
    th {{ background: #f5f5f5; }}
    button {{ background: #1a73e8; color: white; border: none; padding: 10px 24px;
              border-radius: 4px; cursor: pointer; font-size: 1rem; }}
    button:hover {{ background: #1558b0; }}
  </style>
</head>
<body>
  <h1>Select a Google Ads account</h1>
  <p>Choose the account you want Claude to access:</p>
  <form method="POST" action="/oauth/select-account">
    <input type="hidden" name="google_state" value="{google_state}">
    <table>
      <thead><tr><th></th><th>Name</th><th>Account ID</th><th>Type</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
    <button type="submit">Connect this account</button>
  </form>
</body>
</html>"""
    return HTMLResponse(html)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> Response:
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
