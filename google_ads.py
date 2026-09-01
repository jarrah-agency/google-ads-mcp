"""Google Ads API client wrapper — handles both direct and MCC login."""

import logging
import os
from datetime import date

import pandas as pd
from google.ads.googleads.client import GoogleAdsClient

log = logging.getLogger(__name__)

DEVELOPER_TOKEN = os.environ["GOOGLE_ADS_DEVELOPER_TOKEN"]
CLIENT_ID       = os.environ["GOOGLE_ADS_CLIENT_ID"]
CLIENT_SECRET   = os.environ["GOOGLE_ADS_CLIENT_SECRET"]

CHANNEL_TYPE_MAP = {
    "SEARCH": "Search",
    "DISPLAY": "Display",
    "VIDEO": "Video",
    "PERFORMANCE_MAX": "PMax",
    "SHOPPING": "Shopping",
    "MULTI_CHANNEL": "Multi-Channel",
}


def build_client(
    refresh_token: str,
    login_customer_id: str | None = None,
) -> GoogleAdsClient:
    config = {
        "developer_token": DEVELOPER_TOKEN,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token,
        "use_proto_plus": True,
    }
    if login_customer_id:
        config["login_customer_id"] = login_customer_id
    return GoogleAdsClient.load_from_dict(config)


def list_accessible_customers(refresh_token: str) -> list[dict]:
    """Return list of {id, name, is_manager} for all accounts the token can access."""
    client = build_client(refresh_token)
    customer_service = client.get_service("CustomerService")
    response = customer_service.list_accessible_customers()

    accounts = []
    for resource_name in response.resource_names:
        customer_id = resource_name.split("/")[-1]
        try:
            detail = _get_customer_detail(client, customer_id)
            accounts.append(detail)
        except Exception as e:
            log.warning("Could not fetch details for customer %s: %s", customer_id, e)
    return accounts


def _get_customer_detail(client: GoogleAdsClient, customer_id: str) -> dict:
    ga_service = client.get_service("GoogleAdsService")
    query = """
        SELECT
            customer.id,
            customer.descriptive_name,
            customer.manager
        FROM customer
        LIMIT 1
    """
    response = ga_service.search_stream(customer_id=customer_id, query=query)
    for batch in response:
        for row in batch.results:
            return {
                "id": str(row.customer.id),
                "name": row.customer.descriptive_name,
                "is_manager": row.customer.manager,
            }
    return {"id": customer_id, "name": "", "is_manager": False}


def list_client_accounts(
    refresh_token: str,
    manager_id: str,
) -> list[dict]:
    """List client (leaf) accounts under an MCC manager account."""
    client = build_client(refresh_token, login_customer_id=manager_id)
    ga_service = client.get_service("GoogleAdsService")
    query = """
        SELECT
            customer_client.id,
            customer_client.descriptive_name,
            customer_client.manager,
            customer_client.status
        FROM customer_client
        WHERE customer_client.status = 'ENABLED'
            AND customer_client.manager = false
    """
    accounts = []
    response = ga_service.search_stream(customer_id=manager_id, query=query)
    for batch in response:
        for row in batch.results:
            accounts.append({
                "id": str(row.customer_client.id),
                "name": row.customer_client.descriptive_name,
                "status": row.customer_client.status.name,
            })
    return accounts


def list_conversion_actions(
    refresh_token: str,
    customer_id: str,
    login_customer_id: str | None = None,
) -> list[dict]:
    """List enabled conversion actions with 90-day volume."""
    client = build_client(refresh_token, login_customer_id=login_customer_id)
    ga_service = client.get_service("GoogleAdsService")

    action_query = """
        SELECT
            conversion_action.id,
            conversion_action.name,
            conversion_action.type,
            conversion_action.status,
            conversion_action.category
        FROM conversion_action
        WHERE conversion_action.status = 'ENABLED'
    """
    actions = {}
    response = ga_service.search_stream(customer_id=customer_id, query=action_query)
    for batch in response:
        for row in batch.results:
            actions[row.conversion_action.name] = {
                "id": str(row.conversion_action.id),
                "name": row.conversion_action.name,
                "type": row.conversion_action.type.name,
                "category": row.conversion_action.category.name,
                "volume_90d": 0,
            }

    today = date.today()
    start = today.replace(day=1)
    if start.month > 3:
        start = start.replace(month=start.month - 3)
    else:
        start = start.replace(year=start.year - 1, month=start.month + 9)

    volume_query = f"""
        SELECT
            segments.conversion_action_name,
            metrics.all_conversions
        FROM campaign
        WHERE segments.date BETWEEN '{start.isoformat()}' AND '{today.isoformat()}'
    """
    try:
        response = ga_service.search_stream(customer_id=customer_id, query=volume_query)
        for batch in response:
            for row in batch.results:
                name = row.segments.conversion_action_name
                if name in actions:
                    actions[name]["volume_90d"] += row.metrics.all_conversions
    except Exception as e:
        log.warning("Could not fetch conversion volumes: %s", e)

    return sorted(actions.values(), key=lambda a: a["volume_90d"], reverse=True)


def fetch_campaign_metrics(
    refresh_token: str,
    customer_id: str,
    login_customer_id: str | None,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    client = build_client(refresh_token, login_customer_id=login_customer_id)
    ga_service = client.get_service("GoogleAdsService")

    query = f"""
        SELECT
            segments.date,
            campaign.id,
            campaign.name,
            campaign.advertising_channel_type,
            segments.device,
            metrics.cost_micros,
            metrics.impressions,
            metrics.clicks,
            metrics.search_impression_share
        FROM campaign
        WHERE
            segments.date BETWEEN '{start_date.isoformat()}' AND '{end_date.isoformat()}'
            AND campaign.status != 'REMOVED'
    """

    rows = []
    response = ga_service.search_stream(customer_id=customer_id, query=query)
    for batch in response:
        for row in batch.results:
            channel = row.campaign.advertising_channel_type.name
            rows.append({
                "date": row.segments.date,
                "campaign_id": str(row.campaign.id),
                "campaign_name": row.campaign.name,
                "network_type": CHANNEL_TYPE_MAP.get(channel, "Other"),
                "device": row.segments.device.name.title(),
                "cost": row.metrics.cost_micros / 1_000_000,
                "impressions": row.metrics.impressions,
                "clicks": row.metrics.clicks,
                "impression_share": row.metrics.search_impression_share or None,
            })

    if not rows:
        return pd.DataFrame(columns=[
            "date", "campaign_id", "campaign_name", "network_type",
            "device", "cost", "impressions", "clicks", "impression_share",
        ])

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df
