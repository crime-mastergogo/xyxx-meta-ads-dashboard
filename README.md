# XYXX Meta Ads Dashboard

Automated daily statics + videos performance dashboard and weekly Slack reports, powered by Meta Marketing API + GitHub Actions + GitHub Pages.

## What It Does

- **Daily (9am IST):** Pulls last 7 days of ad performance from Meta API, refreshes preview URLs for top statics, rebuilds `docs/index.html` (statics) and `docs/videos.html` (videos)
- **Weekly (Monday 9am IST):** Builds summary report at `docs/weekly/YYYY-WW.html` and sends Slack notification with top performers

## Setup (15 minutes)

### 1. Create the repo

Fork or create a new repo named `xyxx-meta-ads-dashboard` under your GitHub account.

### 2. Add GitHub Secrets

Go to **Settings → Secrets and variables → Actions → New repository secret**:

| Secret name | Value |
|---|---|
| `META_ACCESS_TOKEN` | Your Meta long-lived access token (see below) |
| `META_ACCOUNT_ID` | `act_1857340177852371` |
| `SLACK_WEBHOOK_URL` | Your Slack incoming webhook URL |

### 3. Get a Meta Long-Lived Access Token

1. Go to [developers.facebook.com/tools/explorer](https://developers.facebook.com/tools/explorer)
2. Select your XYXX app → Generate User Token
3. Add permissions: `ads_read`, `ads_management`
4. Exchange for long-lived token (valid ~60 days):
```
curl -X GET "https://graph.facebook.com/oauth/access_token
  ?grant_type=fb_exchange_token
  &client_id=YOUR_APP_ID
  &client_secret=YOUR_APP_SECRET
  &fb_exchange_token=YOUR_SHORT_TOKEN"
```
5. Save the returned token as `META_ACCESS_TOKEN` secret

### 4. Enable GitHub Pages

Go to **Settings → Pages → Source: Deploy from branch → Branch: main → Folder: /docs**

Your dashboard will be live at:
`https://YOUR_USERNAME.github.io/xyxx-meta-ads-dashboard/`

### 5. Get Slack Webhook

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → Create App → Incoming Webhooks
2. Activate and add to your `#performance-reports` channel
3. Copy the webhook URL → save as `SLACK_WEBHOOK_URL` secret

### 6. Trigger first run

Go to **Actions → Daily Meta Ads Dashboard → Run workflow**

## File Structure

```
├── meta_api.py              # Shared Meta API helpers
├── main_statics.py          # Statics data pull
├── main_videos.py           # Videos data pull
├── generate_statics.py      # Statics dashboard HTML builder
├── generate_videos.py       # Videos dashboard HTML builder
├── generate_weekly.py       # Weekly report + Slack sender
├── config.yaml              # Categories, thresholds, settings
├── requirements.txt
├── data/
│   ├── statics_daily.json   # Latest statics data
│   └── videos_daily.json    # Latest videos data
├── docs/                    # GitHub Pages output
│   ├── index.html           # Statics dashboard
│   ├── videos.html          # Videos dashboard
│   └── weekly/              # Weekly report archive
└── .github/workflows/
    ├── daily.yml            # 9am IST daily cron
    └── weekly.yml           # Monday 9am IST weekly cron
```

## Updating Config

Edit `config.yaml` to:
- Add/rename categories (just match adset name keywords)
- Change `top_n_per_category` (default: 10)
- Adjust `min_spend` threshold (default: ₹500)
- Change `daily_lookback_days` (default: 7)

## Token Refresh (every ~60 days)

When Meta token expires, regenerate and update the `META_ACCESS_TOKEN` secret.
The dashboard will fail gracefully and log the error in GitHub Actions.

## Dashboard URLs

- **Statics:** `https://YOUR_USERNAME.github.io/xyxx-meta-ads-dashboard/`
- **Videos:** `https://YOUR_USERNAME.github.io/xyxx-meta-ads-dashboard/videos.html`
- **Weekly Archive:** `https://YOUR_USERNAME.github.io/xyxx-meta-ads-dashboard/weekly/`

## Monthly Report

Runs automatically on the **1st of every month at 9am IST**.

Covers the **full previous calendar month** — all ads, all categories, both statics and videos.

**What it produces:**
- `docs/monthly/YYYY-MM.html` — full deck with 750px statics previews + video thumbnails
- Highlights bar: best static ROAS, highest spender, best video ROAS
- 2-tab layout: Statics (with ★ Best / ⚠ Lowest badges per category) + Videos (ranked by ROAS)
- Slack notification with a "View Monthly Deck →" button
- `docs/monthly/index.html` — rolling archive of all past months

**Monthly report URL:**
`https://YOUR_USERNAME.github.io/xyxx-meta-ads-dashboard/monthly/YYYY-MM.html`

## Full Schedule Summary

| Job | Trigger | Covers | Slack? |
|---|---|---|---|
| `daily.yml` | Every day 9am IST | Last 7 days rolling | ❌ |
| `weekly.yml` | Monday 9am IST | Last 7 days rolling | ✅ with top performers |
| `monthly.yml` | 1st of month 9am IST | Full previous month | ✅ with monthly deck link |
