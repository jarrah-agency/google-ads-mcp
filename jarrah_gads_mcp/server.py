"""Google Ads MCP server.

Exposes three tools:
  - run_gaql            execute any GAQL query against the configured account
  - list_accounts       list all accounts accessible with the refresh token
  - list_client_accounts list leaf accounts under an MCC manager

Credentials are read from environment variables (see .env.example).
"""

import logging
import os

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

CUSTOMER_ID       = os.environ["GOOGLE_ADS_CUSTOMER_ID"]
REFRESH_TOKEN     = os.environ["GOOGLE_ADS_REFRESH_TOKEN"]
LOGIN_CUSTOMER_ID = os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID")  # None for direct accounts

mcp = FastMCP("google-ads")


@mcp.tool()
async def run_gaql(query: str) -> str:
    """Execute a GAQL (Google Ads Query Language) query against the configured account.

    Always include a date filter such as:
      segments.date DURING LAST_30_DAYS
      segments.date BETWEEN 'YYYY-MM-DD' AND 'YYYY-MM-DD'

    Limit results with LIMIT N (max 100).

    Common resources: campaign, ad_group, keyword_view, search_term_view,
    geographic_view, user_location_view, gender_view, age_range_view,
    audience_view, ad_group_ad, conversion_action.
    """
    from jarrah_gads_mcp.google_ads_tools import run_gaql as _run_gaql
    return await _run_gaql(query, CUSTOMER_ID, REFRESH_TOKEN, LOGIN_CUSTOMER_ID)


@mcp.tool()
def list_accounts() -> list[dict]:
    """List all Google Ads accounts accessible with the configured refresh token.

    Returns a list of {id, name, is_manager} dicts.
    Manager accounts (MCC) can be passed to list_client_accounts.
    """
    from jarrah_gads_mcp.google_ads import list_accessible_customers
    return list_accessible_customers(REFRESH_TOKEN)


@mcp.tool()
def list_client_accounts(manager_id: str) -> list[dict]:
    """List leaf (non-manager) accounts under an MCC manager account.

    Args:
        manager_id: the customer ID of the MCC manager account.

    Returns a list of {id, name, status} dicts.
    """
    from jarrah_gads_mcp.google_ads import list_client_accounts as _list_clients
    return _list_clients(REFRESH_TOKEN, manager_id)


@mcp.tool()
def get_campaign_metrics(start_date: str, end_date: str) -> list[dict]:
    """Get campaign performance metrics broken down by date, device, and channel type.

    Args:
        start_date: Start date in YYYY-MM-DD format.
        end_date: End date in YYYY-MM-DD format.

    Returns cost, impressions, clicks, and search impression share per campaign/date/device.
    """
    from datetime import date
    from jarrah_gads_mcp.google_ads import fetch_campaign_metrics
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    df = fetch_campaign_metrics(REFRESH_TOKEN, CUSTOMER_ID, LOGIN_CUSTOMER_ID, start, end)
    records = df.to_dict(orient="records")
    for r in records:
        if hasattr(r.get("date"), "isoformat"):
            r["date"] = r["date"].isoformat()
    return records


@mcp.tool()
def get_conversion_actions() -> list[dict]:
    """List enabled conversion actions with 90-day conversion volume.

    Returns name, type, category, and volume_90d for each conversion action,
    sorted by volume descending.
    """
    from jarrah_gads_mcp.google_ads import list_conversion_actions
    return list_conversion_actions(REFRESH_TOKEN, CUSTOMER_ID, LOGIN_CUSTOMER_ID)


@mcp.tool()
def get_quality_scores() -> list[dict]:
    """Get keyword quality scores with component breakdowns.

    Returns quality_score (1-10), ad_relevance, landing_page_experience, and
    expected_ctr for all active keywords. Component scores are ABOVE_AVERAGE,
    AVERAGE, or BELOW_AVERAGE.
    """
    from jarrah_gads_mcp.google_ads import fetch_quality_scores
    df = fetch_quality_scores(REFRESH_TOKEN, CUSTOMER_ID, LOGIN_CUSTOMER_ID)
    return df.to_dict(orient="records")


@mcp.tool()
def get_ad_creative() -> list[dict]:
    """Get ad creative for all active ads.

    Returns headlines, descriptions, and final URLs for responsive search ads.
    Other ad types return type and status only.
    """
    from jarrah_gads_mcp.google_ads import fetch_ad_creative
    df = fetch_ad_creative(REFRESH_TOKEN, CUSTOMER_ID, LOGIN_CUSTOMER_ID)
    return df.to_dict(orient="records")


def main():
    mcp.run()


if __name__ == "__main__":
    main()
