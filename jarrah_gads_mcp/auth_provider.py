"""MCP OAuth 2.0 provider backed by Google OAuth + Google Ads API.

Flow:
  1. MCP client → GET /authorize  (code_challenge, redirect_uri, state)
  2. authorize() stores pending state, returns Google OAuth URL
  3. User signs in with Google, Google → GET /oauth/google/callback
  4. Callback exchanges Google code for Google refresh token
  5. Lists accessible Google Ads accounts → account picker if >1
  6. Account selected → MCP auth code created → redirect back to MCP client
  7. MCP client → POST /token  (code + code_verifier)
  8. exchange_authorization_code() mints MCP access+refresh tokens carrying
     encrypted Google refresh token + customer_id
  9. Tools call get_access_token() to retrieve per-user credentials
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from typing import Any

from cryptography.fernet import Fernet
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from jarrah_gads_mcp import db as _db

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fernet encryption helpers
# ---------------------------------------------------------------------------

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = os.environ.get("FERNET_KEY", "")
        if not key:
            key = Fernet.generate_key().decode()
            log.warning(
                "FERNET_KEY not set — using ephemeral key; "
                "tokens will be lost on restart."
            )
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def encrypt(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode()).decode()


# ---------------------------------------------------------------------------
# Extended token models carrying Google Ads credentials
# ---------------------------------------------------------------------------

class GadsAuthCode(AuthorizationCode):
    google_rt_enc: str   # encrypted google refresh token
    customer_id: str
    login_customer_id: str | None = None


class GadsAccessToken(AccessToken):
    google_refresh_token: str  # decrypted, ready for API calls
    customer_id: str
    login_customer_id: str | None = None


class GadsRefreshToken(RefreshToken):
    google_rt_enc: str
    customer_id: str
    login_customer_id: str | None = None


# ---------------------------------------------------------------------------
# OAuth provider
# ---------------------------------------------------------------------------

class GoogleAdsOAuthProvider:
    """Implements OAuthAuthorizationServerProvider for Google Ads via Google OAuth."""

    def __init__(self, server_url: str) -> None:
        # server_url is the base URL of this MCP server (e.g. https://my.railway.app)
        self.server_url = server_url.rstrip("/")
        self._google_client_id = os.environ["GOOGLE_ADS_CLIENT_ID"]
        self._google_client_secret = os.environ["GOOGLE_ADS_CLIENT_SECRET"]
        self._google_redirect_uri = f"{self.server_url}/oauth/google/callback"

    # ------------------------------------------------------------------
    # Client registration (dynamic — MCP clients register themselves)
    # ------------------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        row = await asyncio.to_thread(_db.get_client, client_id)
        if row is None:
            return None
        return OAuthClientInformationFull.model_validate(row)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        await asyncio.to_thread(
            _db.save_client,
            client_info.client_id,
            client_info.model_dump(mode="json"),
        )

    # ------------------------------------------------------------------
    # Authorization — redirect to Google OAuth
    # ------------------------------------------------------------------

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        google_state = secrets.token_urlsafe(32)
        expires_at = int(time.time()) + 600  # 10-minute window

        await asyncio.to_thread(
            _db.save_pending,
            google_state,
            params.state,
            client.client_id,
            str(params.redirect_uri),
            params.code_challenge,
            params.scopes or [],
            expires_at,
        )

        from urllib.parse import urlencode
        google_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
            "client_id": self._google_client_id,
            "redirect_uri": self._google_redirect_uri,
            "response_type": "code",
            "scope": "https://www.googleapis.com/auth/adwords",
            "access_type": "offline",
            "prompt": "consent",
            "state": google_state,
        })
        return google_url

    # ------------------------------------------------------------------
    # Authorization code exchange
    # ------------------------------------------------------------------

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> GadsAuthCode | None:
        row = await asyncio.to_thread(_db.get_code, authorization_code)
        if row is None:
            return None
        from pydantic import AnyUrl
        return GadsAuthCode(
            code=row["code"],
            scopes=row["scopes"],
            expires_at=float(row["expires_at"]),
            client_id=row["client_id"],
            code_challenge=row["code_challenge"],
            redirect_uri=AnyUrl(row["redirect_uri"]),
            redirect_uri_provided_explicitly=row["redirect_uri_explicit"],
            resource=row["resource"],
            google_rt_enc=row["google_rt_enc"],
            customer_id=row["customer_id"],
            login_customer_id=row["login_customer_id"],
        )

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: GadsAuthCode,
    ) -> OAuthToken:
        # Consume the code (single-use)
        await asyncio.to_thread(_db.delete_code, authorization_code.code)

        access_token_str = secrets.token_urlsafe(32)
        refresh_token_str = secrets.token_urlsafe(32)
        expires_at = int(time.time()) + 3600  # 1-hour access token

        await asyncio.to_thread(
            _db.save_access_token,
            access_token_str,
            authorization_code.client_id,
            authorization_code.scopes,
            authorization_code.google_rt_enc,
            authorization_code.customer_id,
            authorization_code.login_customer_id,
            expires_at,
        )
        await asyncio.to_thread(
            _db.save_refresh_token,
            refresh_token_str,
            authorization_code.client_id,
            authorization_code.scopes,
            authorization_code.google_rt_enc,
            authorization_code.customer_id,
            authorization_code.login_customer_id,
            None,  # refresh tokens don't expire in our scheme
        )

        return OAuthToken(
            access_token=access_token_str,
            token_type="Bearer",
            expires_in=3600,
            scope=" ".join(authorization_code.scopes),
            refresh_token=refresh_token_str,
        )

    # ------------------------------------------------------------------
    # Token introspection (used by BearerAuthBackend → load_access_token)
    # ------------------------------------------------------------------

    async def load_access_token(self, token: str) -> GadsAccessToken | None:
        row = await asyncio.to_thread(_db.get_access_token_row, token)
        if row is None:
            return None
        return GadsAccessToken(
            token=row["token"],
            client_id=row["client_id"],
            scopes=row["scopes"],
            expires_at=row["expires_at"],
            google_refresh_token=decrypt(row["google_rt_enc"]),
            customer_id=row["customer_id"],
            login_customer_id=row["login_customer_id"],
        )

    # ------------------------------------------------------------------
    # Refresh token exchange
    # ------------------------------------------------------------------

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> GadsRefreshToken | None:
        row = await asyncio.to_thread(_db.get_refresh_token_row, refresh_token)
        if row is None:
            return None
        return GadsRefreshToken(
            token=row["token"],
            client_id=row["client_id"],
            scopes=row["scopes"],
            expires_at=row["expires_at"],
            google_rt_enc=row["google_rt_enc"],
            customer_id=row["customer_id"],
            login_customer_id=row["login_customer_id"],
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: GadsRefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        # Rotate tokens
        await asyncio.to_thread(_db.delete_refresh_token, refresh_token.token)

        new_access = secrets.token_urlsafe(32)
        new_refresh = secrets.token_urlsafe(32)
        expires_at = int(time.time()) + 3600

        use_scopes = scopes or refresh_token.scopes

        await asyncio.to_thread(
            _db.save_access_token,
            new_access,
            refresh_token.client_id,
            use_scopes,
            refresh_token.google_rt_enc,
            refresh_token.customer_id,
            refresh_token.login_customer_id,
            expires_at,
        )
        await asyncio.to_thread(
            _db.save_refresh_token,
            new_refresh,
            refresh_token.client_id,
            use_scopes,
            refresh_token.google_rt_enc,
            refresh_token.customer_id,
            refresh_token.login_customer_id,
            None,
        )

        return OAuthToken(
            access_token=new_access,
            token_type="Bearer",
            expires_in=3600,
            scope=" ".join(use_scopes),
            refresh_token=new_refresh,
        )

    # ------------------------------------------------------------------
    # Revocation
    # ------------------------------------------------------------------

    async def revoke_token(
        self,
        token: GadsAccessToken | GadsRefreshToken,
    ) -> None:
        if isinstance(token, GadsAccessToken):
            await asyncio.to_thread(_db.delete_access_token, token.token)
        else:
            await asyncio.to_thread(_db.delete_refresh_token, token.token)
