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
    from google_ads_tools import run_gaql as _run_gaql
    return await _run_gaql(query, CUSTOMER_ID, REFRESH_TOKEN, LOGIN_CUSTOMER_ID)


@mcp.tool()
def list_accounts() -> list[dict]:
    """List all Google Ads accounts accessible with the configured refresh token.

    Returns a list of {id, name, is_manager} dicts.
    Manager accounts (MCC) can be passed to list_client_accounts.
    """
    from google_ads import list_accessible_customers
    return list_accessible_customers(REFRESH_TOKEN)


@mcp.tool()
def list_client_accounts(manager_id: str) -> list[dict]:
    """List leaf (non-manager) accounts under an MCC manager account.

    Args:
        manager_id: the customer ID of the MCC manager account.

    Returns a list of {id, name, status} dicts.
    """
    from google_ads import list_client_accounts as _list_clients
    return _list_clients(REFRESH_TOKEN, manager_id)


if __name__ == "__main__":
    mcp.run()
