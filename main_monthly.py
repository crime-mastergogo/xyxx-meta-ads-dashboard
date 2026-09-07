"""
main_monthly.py — Full previous month data pull.

Key behaviours:
- Pulls full previous calendar month from Meta API
- CONSOLIDATES by ad_name across all campaigns/ad sets (same creative = one row)
- Filters to ads launched in the current month (name prefix match)
- Saves statics_monthly_YYYY-MM.json + videos_monthly_YYYY-MM.json
"""

import json
import os
import re
import yaml
import requests
from datetime import datetime, date, timedelta
from collections import defaultdict

BASE_DIR = os.path.dirname(__file__)
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
TOKEN = os.environ["META_ACCESS_TOKEN"]
ACCOUNT_ID = os.environ.get("META_ACCOUNT_ID", "act_1857340177852371")
API_VERSION = "v19.0"
BASE_URL = f"https://graph.facebook.com/{API_VERSION}"


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def prev_month_range():
    today = date.today()
    first_this = today.replace(day=1)
    last_prev = first_this - timedelta(days=1)
    first_prev = last_prev.replace(day=1)
    month_str = last_prev.strftime("%Y-%m")
    month_prefix = last_prev.strftime("%B%Y-")  # e.g. "August2026-"
    return str(first_prev), str(last_prev), month_str, month_prefix


def fetch_all_ads(since, until, min_spend):
    """Pull all ad-level insights for the month, return raw list."""
    params = {
        "level": "ad",
        "fields": "ad_id,ad_name,adset_name,spend,purchase_roas,purchase_conversion_value,actions",
        "time_range": json.dumps({"since": since, "until": until}),
        "action_attribution_windows": json.dumps(["1d_click"]),
        "limit": 500,
        "access_token": TOKEN,
    }
    results = []
    url = f"{BASE_URL}/{ACCOUNT_ID}/insights"
    while True:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        for row in data.get("data", []):
            spend = float(row.get("spend", 0))
            if spend < 1:
                continue
            roas_list = row.get("purchase_roas", [])
            roas = float(roas_list[0]["value"]) if roas_list else 0.0
            purchases = 0
            for action in row.get("actions", []):
                if action.get("action_type") == "purchase":
                    purchases = int(float(action.get("1d_click", action.get("value", 0))))
                    break
            cv = float(row.get("purchase_conversion_value", 0))
            results.append({
                "ad_id":      row.get("ad_id", ""),
                "ad_name":    row.get("ad_name", ""),
                "adset_name": row.get("adset_name", ""),
                "spend":      spend,
                "roas":       roas,
                "conv_value": cv,
                "purchases":  purchases,
            })
        paging = data.get("paging", {})
        if not paging.get("next"):
            break
        params["after"] = paging.get("cursors", {}).get("after")
    return results


def consolidate_by_creative(ads):
    """
    Group rows by ad_name across all campaigns/ad sets.
    Sums spend, conv_value, purchases.
    Recalculates blended ROAS = total_cv / total_spend.
    Keeps the ad_id and adset_name from the highest-spend row (for preview fetch).
    Returns list of consolidated ad dicts.
    """
    groups = defaultdict(lambda: {
        "ad_id": "", "adset_name": "", "spend": 0.0,
        "conv_value": 0.0, "purchases": 0, "_max_spend": 0.0
    })
    for ad in ads:
        name = ad["ad_name"]
        g = groups[name]
        g["spend"]      += ad["spend"]
        g["conv_value"] += ad["conv_value"]
        g["purchases"]  += ad["purchases"]
        # Keep ad_id/adset from the highest individual spend row (best preview)
        if ad["spend"] > g["_max_spend"]:
            g["_max_spend"]  = ad["spend"]
            g["ad_id"]       = ad["ad_id"]
            g["adset_name"]  = ad["adset_name"]

    consolidated = []
    for name, g in groups.items():
        blended_roas = g["conv_value"] / g["spend"] if g["spend"] > 0 else 0.0
        consolidated.append({
            "ad_name":    name,
            "ad_id":      g["ad_id"],
            "adset_name": g["adset_name"],
            "spend":      round(g["spend"], 2),
            "roas":       round(blended_roas, 4),
            "conv_value": round(g["conv_value"], 2),
            "purchases":  g["purchases"],
        })
    return consolidated


def filter_this_month(ads, month_prefix):
    """Keep only ads whose name starts with this month's prefix (e.g. August2026-)."""
    prefix_lower = month_prefix.lower()
    return [a for a in ads if a["ad_name"].lower().startswith(prefix_lower)]


def is_static(ad):
    adset = ad["adset_name"].lower()
    name  = ad["ad_name"].lower()
    if "video" in adset:
        return False
    if any(kw in name for kw in ["-video-", "_video_", "ugc-pa", "ugc-diy", "ugc-dark"]):
        return False
    return True


def is_video(ad):
    adset = ad["adset_name"].lower()
    name  = ad["ad_name"].lower()
    if "video" in adset:
        return True
    if any(kw in name for kw in ["-video-", "_video_", "ugc-pa", "ugc-diy", "ugc-dark"]):
        return True
    return False


def categorise(ads, categories):
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


def fetch_preview(ad_id):
    try:
        params = {"ad_format": "MOBILE_FEED_STANDARD", "access_token": TOKEN}
        r = requests.get(f"{BASE_URL}/{ad_id}/previews", params=params, timeout=15)
        r.raise_for_status()
        items = r.json().get("data", [])
        if items:
            m = re.search(r'src="([^"]+)"', items[0].get("body", ""))
            if m:
                return m.group(1).replace("&amp;", "&")
    except Exception as e:
        print(f"  Preview failed {ad_id}: {e}")
    return ""


def build_category_summary(by_cat, categories, top_n, min_spend_lowest, fetch_previews=True):
    """
    For each category:
    - top_ads: top N by spend (consolidated)
    - worst_ads: bottom 10 by ROAS with spend >= min_spend_lowest
    Fetches preview URLs for both sets.
    """
    summary = {}
    for cat, ads in by_cat.items():
        if cat == "Other":
            continue
        emoji = categories.get(cat, {}).get("emoji", "")
        total_spend = sum(a["spend"] for a in ads)
        total_cv    = sum(a["conv_value"] for a in ads)
        blended     = total_cv / total_spend if total_spend > 0 else 0

        # Top performers by spend
        top_ads = sorted(ads, key=lambda x: x["spend"], reverse=True)[:top_n]

        # Bottom 10 by ROAS (min ₹10K spend, must have purchases)
        qualified_for_worst = [
            a for a in ads
            if a["spend"] >= min_spend_lowest and a["purchases"] > 0 and a["roas"] > 0
        ]
        worst_ads = sorted(qualified_for_worst, key=lambda x: x["roas"])[:10]

        # Fetch preview URLs for all unique ads
        if fetch_previews:
            all_ids = list({a["ad_id"]: a for a in top_ads + worst_ads if a["ad_id"]}.keys())
            print(f"  [{cat}] Fetching {len(all_ids)} preview URLs...")
            preview_map = {}
            import time
            for ad_id in all_ids:
                preview_map[ad_id] = fetch_preview(ad_id)
                time.sleep(0.25)
            for a in top_ads + worst_ads:
                a["preview_url"] = preview_map.get(a["ad_id"], "")

        summary[cat] = {
            "emoji":        emoji,
            "total_ads":    len(ads),
            "total_spend":  total_spend,
            "blended_roas": blended,
            "top_ads":      top_ads,
            "worst_ads":    worst_ads,
        }
    return summary


def run():
    cfg = load_config()
    since, until, month_str, month_prefix = prev_month_range()
    top_n           = cfg.get("top_n_per_category", 10)
    min_spend_worst = 10000  # ₹10K minimum for lowest performers

    print(f"[Monthly] Period: {since} → {until}  (prefix: {month_prefix})")

    print("[Monthly] Pulling all ad insights...")
    raw_ads = fetch_all_ads(since, until, min_spend=0)
    print(f"[Monthly] Raw rows: {len(raw_ads)}")

    # Consolidate across campaigns/ad sets
    consolidated = consolidate_by_creative(raw_ads)
    print(f"[Monthly] After consolidation: {len(consolidated)} unique creatives")

    # Filter to this month's launches only
    this_month = filter_this_month(consolidated, month_prefix)
    print(f"[Monthly] This month's creatives: {len(this_month)}")

    # Split statics / videos
    statics = [a for a in this_month if is_static(a)]
    videos  = [a for a in this_month if is_video(a)]
    print(f"[Monthly] Statics: {len(statics)} · Videos: {len(videos)}")

    # Categorise
    s_by_cat = categorise(statics, cfg["statics_categories"])
    v_by_cat = categorise(videos,  cfg["videos_categories"])

    # Build summaries with previews
    print("[Monthly] Building statics summary...")
    s_summary = build_category_summary(
        s_by_cat, cfg["statics_categories"], top_n, min_spend_worst
    )

    print("[Monthly] Building videos summary...")
    v_summary = build_category_summary(
        v_by_cat, cfg["videos_categories"], top_n, min_spend_worst
    )

    # Save
    os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)

    s_out = {
        "generated_at": datetime.utcnow().isoformat(),
        "month": month_str, "date_since": since, "date_until": until,
        "month_prefix": month_prefix,
        "min_spend_worst": min_spend_worst,
        "categories": s_summary,
    }
    v_out = {
        "generated_at": datetime.utcnow().isoformat(),
        "month": month_str, "date_since": since, "date_until": until,
        "month_prefix": month_prefix,
        "min_spend_worst": min_spend_worst,
        "categories": v_summary,
    }

    s_path = os.path.join(BASE_DIR, "data", f"statics_monthly_{month_str}.json")
    v_path = os.path.join(BASE_DIR, "data", f"videos_monthly_{month_str}.json")

    with open(s_path, "w") as f:
        json.dump(s_out, f, indent=2)
    with open(v_path, "w") as f:
        json.dump(v_out, f, indent=2)

    print(f"[Monthly] Saved → {s_path}")
    print(f"[Monthly] Saved → {v_path}")


if __name__ == "__main__":
    run()
