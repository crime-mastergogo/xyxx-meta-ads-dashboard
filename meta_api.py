"""
meta_api.py — Shared Meta Marketing API helpers.
"""

import os
import time
import re
import requests
import json
from datetime import datetime, timedelta
from collections import defaultdict

TOKEN = os.environ["META_ACCESS_TOKEN"]
ACCOUNT_ID = os.environ.get("META_ACCOUNT_ID", "act_1857340177852371")
API_VERSION = "v19.0"
BASE = f"https://graph.facebook.com/{API_VERSION}"


def _get(path, params):
    params = dict(params)
    params["access_token"] = TOKEN
    r = requests.get(f"{BASE}{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def date_range(lookback_days):
    end = datetime.utcnow().date() - timedelta(days=1)
    start = end - timedelta(days=lookback_days - 1)
    return str(start), str(end)


def fetch_ad_insights(lookback_days=7, ad_name_prefix=None, extra_fields=None):
    """
    Pull ad-level insights from Meta Marketing API.
    Uses async insights job to avoid 400 errors on large accounts.
    """
    since, until = date_range(lookback_days)

    # Step 1: Create async report job
    job_params = {
        "level": "ad",
        "fields": "ad_id,ad_name,adset_name,spend,purchase_roas,purchase_conversion_value,actions",
        "time_range": json.dumps({"since": since, "until": until}),
        "limit": 500,
        "access_token": TOKEN,
    }

    # Submit async job
    r = requests.post(
        f"{BASE}/{ACCOUNT_ID}/insights",
        data=job_params,
        timeout=30
    )
    r.raise_for_status()
    job_id = r.json().get("report_run_id")
    print(f"  Async job submitted: {job_id}")

    # Step 2: Poll until complete
    import time as _time
    for attempt in range(60):
        _time.sleep(5)
        status_r = requests.get(
            f"{BASE}/{job_id}",
            params={"access_token": TOKEN},
            timeout=15
        )
        status_r.raise_for_status()
        status = status_r.json()
        pct = status.get("async_percent_completion", 0)
        print(f"  Job progress: {pct}%")
        if status.get("async_status") == "Job Completed":
            break
    else:
        raise RuntimeError("Async insights job timed out after 5 minutes")

    # Step 3: Fetch results
    results = []
    results_params = {
        "access_token": TOKEN,
        "limit": 500,
    }
    results_url = f"{BASE}/{job_id}/insights"

    while True:
        res_r = requests.get(results_url, params=results_params, timeout=30)
        res_r.raise_for_status()
        data = res_r.json()

        for row in data.get("data", []):
            spend = float(row.get("spend", 0))
            if spend < 1:
                continue

            ad_name = row.get("ad_name", "")
            if ad_name_prefix and not ad_name.startswith(ad_name_prefix):
                continue

            roas_list = row.get("purchase_roas", [])
            roas = float(roas_list[0]["value"]) if roas_list else 0.0

            purchases = 0
            for action in row.get("actions", []):
                if action.get("action_type") == "purchase":
                    val = action.get("1d_click") or action.get("value", 0)
                    purchases = int(float(val))
                    break

            cv = float(row.get("purchase_conversion_value", 0))

            results.append({
                "ad_id":      row.get("ad_id", ""),
                "ad_name":    ad_name,
                "adset_name": row.get("adset_name", ""),
                "spend":      spend,
                "roas":       roas,
                "conv_value": cv,
                "purchases":  purchases,
                "date_since": since,
                "date_until": until,
            })

        paging = data.get("paging", {})
        if not paging.get("next"):
            break
        results_params["after"] = paging.get("cursors", {}).get("after")

    print(f"  Total rows fetched: {len(results)}")
    return results


def fetch_preview_url(ad_id, ad_format="MOBILE_FEED_STANDARD"):
    try:
        data = _get(f"/{ad_id}/previews", {"ad_format": ad_format})
        items = data.get("data", [])
        if items:
            iframe_html = items[0].get("body", "")
            m = re.search(r'src="([^"]+)"', iframe_html)
            if m:
                return m.group(1).replace("&amp;", "&")
    except Exception as e:
        print(f"  Preview fetch failed for {ad_id}: {e}")
    return None


def fetch_previews_bulk(ad_ids, delay=0.3):
    preview_map = {}
    for i, ad_id in enumerate(ad_ids):
        url = fetch_preview_url(ad_id)
        if url:
            preview_map[ad_id] = url
        if delay and i < len(ad_ids) - 1:
            time.sleep(delay)
    print(f"  Fetched {len(preview_map)}/{len(ad_ids)} preview URLs")
    return preview_map


def categorise_ads(ads, categories):
    by_cat = defaultdict(list)
    for ad in ads:
        adset = ad["adset_name"].lower()
        matched = False
        for cat_name, cfg in categories.items():
            if any(kw.lower() in adset for kw in cfg["adset_contains"]):
                by_cat[cat_name].append(ad)
                matched = True
                break
        if not matched:
            by_cat["Other"].append(ad)
    return dict(by_cat)


def consolidate_by_creative(ads):
    groups = defaultdict(lambda: {
        "ad_id": "", "adset_name": "", "spend": 0.0,
        "conv_value": 0.0, "purchases": 0, "_max_spend": 0.0,
        "date_since": "", "date_until": "",
    })
    for ad in ads:
        name = ad["ad_name"]
        g = groups[name]
        g["spend"]      += ad["spend"]
        g["conv_value"] += ad["conv_value"]
        g["purchases"]  += ad["purchases"]
        if ad["spend"] > g["_max_spend"]:
            g["_max_spend"]  = ad["spend"]
            g["ad_id"]       = ad["ad_id"]
            g["adset_name"]  = ad["adset_name"]
        g["date_since"] = ad.get("date_since", "")
        g["date_until"] = ad.get("date_until", "")

    result = []
    for name, g in groups.items():
        roas = g["conv_value"] / g["spend"] if g["spend"] > 0 else 0.0
        result.append({
            "ad_name":    name,
            "ad_id":      g["ad_id"],
            "adset_name": g["adset_name"],
            "spend":      round(g["spend"], 2),
            "roas":       round(roas, 4),
            "conv_value": round(g["conv_value"], 2),
            "purchases":  g["purchases"],
            "date_since": g["date_since"],
            "date_until": g["date_until"],
        })
    return result
