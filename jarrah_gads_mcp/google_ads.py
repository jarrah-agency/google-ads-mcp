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


def fetch_quality_scores(
    refresh_token: str,
    customer_id: str,
    login_customer_id: str | None = None,
) -> pd.DataFrame:
    """Fetch keyword quality scores with component breakdowns."""
    client = build_client(refresh_token, login_customer_id=login_customer_id)
    ga_service = client.get_service("GoogleAdsService")

    query = """
        SELECT
            campaign.id,
            campaign.name,
            ad_group.id,
            ad_group.name,
            ad_group_criterion.keyword.text,
            ad_group_criterion.keyword.match_type,
            ad_group_criterion.quality_info.quality_score,
            ad_group_criterion.quality_info.creative_quality_score,
            ad_group_criterion.quality_info.post_click_quality_score,
            ad_group_criterion.quality_info.search_predicted_ctr
        FROM keyword_view
        WHERE ad_group_criterion.status != 'REMOVED'
            AND campaign.status != 'REMOVED'
            AND ad_group.status != 'REMOVED'
            AND ad_group_criterion.quality_info.quality_score > 0
    """

    rows = []
    response = ga_service.search_stream(customer_id=customer_id, query=query)
    for batch in response:
        for row in batch.results:
            qi = row.ad_group_criterion.quality_info
            rows.append({
                "campaign_id": str(row.campaign.id),
                "campaign_name": row.campaign.name,
                "ad_group_id": str(row.ad_group.id),
                "ad_group_name": row.ad_group.name,
                "keyword": row.ad_group_criterion.keyword.text,
                "match_type": row.ad_group_criterion.keyword.match_type.name,
                "quality_score": qi.quality_score,
                "ad_relevance": qi.creative_quality_score.name,
                "landing_page_experience": qi.post_click_quality_score.name,
                "expected_ctr": qi.search_predicted_ctr.name,
            })

    if not rows:
        return pd.DataFrame(columns=[
            "campaign_id", "campaign_name", "ad_group_id", "ad_group_name",
            "keyword", "match_type", "quality_score", "ad_relevance",
            "landing_page_experience", "expected_ctr",
        ])

    return pd.DataFrame(rows)


def generate_keyword_ideas(
    refresh_token: str,
    customer_id: str,
    login_customer_id: str | None,
    keywords: list[str],
    url: str | None = None,
    language_id: int = 1000,  # English
    country_code: str = "US",
    page_size: int = 100,
) -> list[dict]:
    """Generate keyword ideas using KeywordPlanIdeaService.

    Returns a list of dicts with keyword, avg_monthly_searches, competition,
    low_bid_micros, high_bid_micros.

    language_id: Google language criterion ID (1000=English, 1001=French, etc.)
    country_code: ISO 3166-1 alpha-2 country code used to look up geo target.
    """
    client = build_client(refresh_token, login_customer_id=login_customer_id)
    kp_idea_service = client.get_service("KeywordPlanIdeaService")
    geo_target_service = client.get_service("GeoTargetConstantService")

    # Resolve country code → geo target resource name
    suggest_req = client.get_type("SuggestGeoTargetConstantsRequest")
    suggest_req.locale = "en"
    suggest_req.country_code = country_code
    geo_resp = geo_target_service.suggest_geo_target_constants(request=suggest_req)
    geo_target = None
    for suggestion in geo_resp.geo_target_constant_suggestions:
        g = suggestion.geo_target_constant
        if g.target_type == "Country":
            geo_target = g.resource_name
            break
    if geo_target is None and geo_resp.geo_target_constant_suggestions:
        geo_target = geo_resp.geo_target_constant_suggestions[0].geo_target_constant.resource_name

    language_resource = f"languageConstants/{language_id}"

    request = client.get_type("GenerateKeywordIdeasRequest")
    request.customer_id = customer_id.replace("-", "")
    request.language = language_resource
    if geo_target:
        request.geo_target_constants.append(geo_target)
    request.include_adult_keywords = False
    request.page_size = page_size

    if keywords and url:
        seed = client.get_type("KeywordAndUrlSeed")
        seed.url = url
        seed.keywords.extend(keywords)
        request.keyword_and_url_seed.CopyFrom(seed)
    elif url:
        seed = client.get_type("UrlSeed")
        seed.url = url
        request.url_seed.CopyFrom(seed)
    else:
        seed = client.get_type("KeywordSeed")
        seed.keywords.extend(keywords)
        request.keyword_seed.CopyFrom(seed)

    COMPETITION_MAP = {0: "UNSPECIFIED", 1: "UNKNOWN", 2: "LOW", 3: "MEDIUM", 4: "HIGH"}

    results = []
    response = kp_idea_service.generate_keyword_ideas(request=request)
    for idea in response:
        m = idea.keyword_idea_metrics
        results.append({
            "keyword": idea.text,
            "avg_monthly_searches": m.avg_monthly_searches,
            "competition": COMPETITION_MAP.get(int(m.competition), "UNKNOWN"),
            "low_top_of_page_bid": round(m.low_top_of_page_bid_micros / 1_000_000, 2) if m.low_top_of_page_bid_micros else None,
            "high_top_of_page_bid": round(m.high_top_of_page_bid_micros / 1_000_000, 2) if m.high_top_of_page_bid_micros else None,
        })

    results.sort(key=lambda r: r["avg_monthly_searches"] or 0, reverse=True)
    return results


def get_keyword_historical_metrics(
    refresh_token: str,
    customer_id: str,
    login_customer_id: str | None,
    keywords: list[str],
    language_id: int = 1000,
    country_code: str = "US",
) -> list[dict]:
    """Get historical metrics for a specific list of keywords.

    Returns avg_monthly_searches, competition, and bid estimates for each keyword.
    """
    client = build_client(refresh_token, login_customer_id=login_customer_id)
    kp_idea_service = client.get_service("KeywordPlanIdeaService")
    geo_target_service = client.get_service("GeoTargetConstantService")

    suggest_req = client.get_type("SuggestGeoTargetConstantsRequest")
    suggest_req.locale = "en"
    suggest_req.country_code = country_code
    geo_resp = geo_target_service.suggest_geo_target_constants(request=suggest_req)
    geo_target = None
    for suggestion in geo_resp.geo_target_constant_suggestions:
        g = suggestion.geo_target_constant
        if g.target_type == "Country":
            geo_target = g.resource_name
            break
    if geo_target is None and geo_resp.geo_target_constant_suggestions:
        geo_target = geo_resp.geo_target_constant_suggestions[0].geo_target_constant.resource_name

    language_resource = f"languageConstants/{language_id}"

    request = client.get_type("GenerateKeywordIdeasRequest")
    request.customer_id = customer_id.replace("-", "")
    request.language = language_resource
    if geo_target:
        request.geo_target_constants.append(geo_target)
    request.include_adult_keywords = False
    request.page_size = len(keywords)

    seed = client.get_type("KeywordSeed")
    seed.keywords.extend(keywords)
    request.keyword_seed.CopyFrom(seed)

    COMPETITION_MAP = {0: "UNSPECIFIED", 1: "UNKNOWN", 2: "LOW", 3: "MEDIUM", 4: "HIGH"}

    results = []
    response = kp_idea_service.generate_keyword_ideas(request=request)
    for idea in response:
        m = idea.keyword_idea_metrics
        results.append({
            "keyword": idea.text,
            "avg_monthly_searches": m.avg_monthly_searches,
            "competition": COMPETITION_MAP.get(int(m.competition), "UNKNOWN"),
            "low_top_of_page_bid": round(m.low_top_of_page_bid_micros / 1_000_000, 2) if m.low_top_of_page_bid_micros else None,
            "high_top_of_page_bid": round(m.high_top_of_page_bid_micros / 1_000_000, 2) if m.high_top_of_page_bid_micros else None,
        })

    return results


def fetch_ad_creative(
    refresh_token: str,
    customer_id: str,
    login_customer_id: str | None = None,
) -> pd.DataFrame:
    """Fetch ad creative for all non-removed ads (RSA headlines/descriptions + final URLs)."""
    client = build_client(refresh_token, login_customer_id=login_customer_id)
    ga_service = client.get_service("GoogleAdsService")

    query = """
        SELECT
            campaign.id,
            campaign.name,
            ad_group.id,
            ad_group.name,
            ad_group_ad.ad.id,
            ad_group_ad.ad.type,
            ad_group_ad.ad.final_urls,
            ad_group_ad.ad.responsive_search_ad.headlines,
            ad_group_ad.ad.responsive_search_ad.descriptions,
            ad_group_ad.status
        FROM ad_group_ad
        WHERE ad_group_ad.status != 'REMOVED'
            AND campaign.status != 'REMOVED'
            AND ad_group.status != 'REMOVED'
    """

    rows = []
    response = ga_service.search_stream(customer_id=customer_id, query=query)
    for batch in response:
        for row in batch.results:
            ad = row.ad_group_ad.ad
            ad_type = ad.type_.name
            headlines = []
            descriptions = []
            if ad_type == "RESPONSIVE_SEARCH_AD":
                headlines = [h.text for h in ad.responsive_search_ad.headlines]
                descriptions = [d.text for d in ad.responsive_search_ad.descriptions]
            rows.append({
                "campaign_id": str(row.campaign.id),
                "campaign_name": row.campaign.name,
                "ad_group_id": str(row.ad_group.id),
                "ad_group_name": row.ad_group.name,
                "ad_id": str(ad.id),
                "ad_type": ad_type,
                "status": row.ad_group_ad.status.name,
                "final_urls": list(ad.final_urls),
                "headlines": headlines,
                "descriptions": descriptions,
            })

    if not rows:
        return pd.DataFrame(columns=[
            "campaign_id", "campaign_name", "ad_group_id", "ad_group_name",
            "ad_id", "ad_type", "status", "final_urls", "headlines", "descriptions",
        ])

    return pd.DataFrame(rows)
