"""Google Ads GAQL tool — async function that executes arbitrary GAQL queries."""

from __future__ import annotations

import asyncio
import json
import logging

log = logging.getLogger(__name__)


async def run_gaql(
    query: str,
    customer_id: str,
    refresh_token: str,
    login_customer_id: str | None = None,
) -> str:
    """Execute a GAQL query and return results as a JSON string."""

    def _sync() -> list[dict] | str:
        from google.ads.googleads.errors import GoogleAdsException
        from google_ads import build_client

        client = build_client(refresh_token, login_customer_id)
        ga_service = client.get_service("GoogleAdsService")
        cid = customer_id.replace("-", "")

        try:
            response = ga_service.search(customer_id=cid, query=query)
            rows = []
            for row in response:
                row_dict = type(row).to_dict(row)
                rows.append(_compact(row_dict))
            return rows
        except GoogleAdsException as exc:
            errors = [e.message for e in exc.failure.errors]
            return f"Google Ads API error: {'; '.join(errors)}"
        except Exception as exc:
            return f"Error: {exc}"

    result = await asyncio.to_thread(_sync)

    if isinstance(result, str):
        log.warning("GAQL error for customer %s: %s", customer_id, result)
        return result

    if not result:
        return "Query returned no results."

    truncated = False
    if len(result) > 100:
        result = result[:100]
        truncated = True

    output = json.dumps(result, indent=2, default=str)
    if truncated:
        output += "\n\n[Results truncated to 100 rows]"
    log.info("GAQL returned %d rows for customer %s", len(result), customer_id)
    return output


def _compact(d: dict) -> dict:
    """Recursively drop keys whose value is an empty dict, empty list, or None."""
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            v = _compact(v)
            if v:
                out[k] = v
        elif isinstance(v, list):
            if v:
                out[k] = v
        elif v is not None and v != 0 and v != "" and v is not False:
            out[k] = v
    return out
