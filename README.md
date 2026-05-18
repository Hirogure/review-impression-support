# meo-mini-ops

Internal project:

```text
PROJECT: Google口コミ/MEOミニ運用
```

External service name:

```text
Google口コミ返信・印象改善サポート
```

## Goal

Build a small, semi-automated operation to reach monthly profit of 300,000 JPY.

Initial model:

- Lead discovery automation
- Manual review by human
- Free Google review page check
- 3 sample review replies
- Monthly support offer

Initial target:

```text
整体・接骨院
```

Initial offer:

```text
Google口コミの無料チェック + 返信文3件サンプル
```

Recommended price:

```text
First month: 9,800 JPY
Regular: 29,800 JPY/month
```

## Setup

PowerShell:

```powershell
$env:GOOGLE_MAPS_API_KEY="YOUR_API_KEY"
cd C:\Users\hiroa\Documents\Codex\2026-05-17\30-1-3\meo-mini-ops
python lead_finder.py --areas "新宿区" --keyword "整体 接骨院" --limit 5
```

The API key is only set for the current PowerShell session. If you close PowerShell, set it again.

## Output

CSV files are created under:

```text
outputs/
```

Main columns:

- name
- rating
- user_ratings_total
- website
- phone
- google_maps_url
- contact_page_url
- contact_form_url
- contact_email
- instagram_url
- line_url
- chain_like
- chain_reason
- score
- score_reason
- 確認ステータス
- 直近口コミ
- 返信状況
- 低評価状況
- 営業実在
- 連絡導線
- 判断
- メモ

## Manual Review

Do not send outreach directly from the raw CSV.

First review high-score rows where:

```text
chain_like = no
contact_form_url or contact_email exists
```

Then fill the manual columns using:

```text
templates/manual_check_guide.md
```

You can also use the local triage UI:

```powershell
python review_triage.py outputs\leads_YYYYMMDD_HHMMSS.csv
```

The UI opens in your browser and saves decisions back to the CSV.

Decision values:

```text
送信A: かなり送ってよさそう
送信B: 再度内容確認した方がよい
保留
除外
```

To list sendable leads:

```powershell
python list_sendable.py outputs\leads_YYYYMMDD_HHMMSS.csv
```

Basic send criteria:

```text
chain_like=no
manual_recent_reviews=あり
manual_reply_status=未返信多い or 一部未返信
manual_low_rating_status=放置あり
manual_business_alive=あり
manual_contact_ok=フォーム or メール
```

Exclude when:

```text
直近10件が全返信
星1〜3口コミなし
最新口コミが1年以上前
Webサイトなし / 連絡導線なし
営業実在が不明
チェーン/複数店舗運営っぽい
```

## Initial KPI

```text
2 weeks:
100 leads extracted
30 manually reviewed
30 outreach sent
5 replies
2 meetings
1 paid customer
```

## Safety Rules

- Do not scrape the Google Maps screen directly.
- Do not send mass automated messages.
- Human must approve every outreach message.
- Do not say "we will increase your reviews" or "we will improve your rating."
- Do not criticize the store in the free check.
