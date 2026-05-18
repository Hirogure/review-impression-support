import argparse
import csv
import datetime as dt
import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


API_BASE = "https://maps.googleapis.com/maps/api/place"
OUTPUT_DIR = Path("outputs")

CHAIN_NAME_PATTERNS = [
    "グループ",
    "チェーン",
    "ホールディングス",
    "株式会社",
    "FC",
    "フランチャイズ",
]

CHAIN_URL_PATTERNS = [
    "egao-do.com",
    "hinode-sekkotsu",
    "karadafactory",
    "rebody",
    "kumanomi",
    "icure",
    "relax",
    "reraku",
]

CONTACT_PATH_HINTS = [
    "contact",
    "inquiry",
    "reserve",
    "reservation",
    "booking",
    "form",
    "otoiawase",
    "toiawase",
    "お問い合わせ",
    "予約",
    "問合せ",
]

IGNORE_CONTACT_URL_PARTS = [
    "/wp-content/",
    "/wp-includes/",
    "/wp-json/",
    "/assets/",
    "/static/",
    "/css/",
    "/js/",
    ".css",
    ".js",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".xml",
    "oembed",
]


def http_get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as response:
        body = response.read().decode("utf-8")
    return json.loads(body)


def http_get_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; meo-mini-ops/0.1; lead research)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        content_type = response.headers.get("Content-Type", "")
        charset = "utf-8"
        match = re.search(r"charset=([\w\-]+)", content_type, re.I)
        if match:
            charset = match.group(1)
        raw = response.read(600_000)
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def call_places_text_search(api_key: str, query: str) -> list[dict]:
    params = {
        "query": query,
        "language": "ja",
        "region": "jp",
        "key": api_key,
    }
    url = f"{API_BASE}/textsearch/json?{urllib.parse.urlencode(params)}"
    data = http_get_json(url)
    status = data.get("status")
    if status not in {"OK", "ZERO_RESULTS"}:
        raise RuntimeError(f"Text Search failed: {status} {data.get('error_message', '')}")
    return data.get("results", [])


def call_place_details(api_key: str, place_id: str) -> dict:
    fields = ",".join(
        [
            "name",
            "formatted_address",
            "formatted_phone_number",
            "international_phone_number",
            "website",
            "url",
            "rating",
            "user_ratings_total",
            "business_status",
            "types",
        ]
    )
    params = {
        "place_id": place_id,
        "fields": fields,
        "language": "ja",
        "key": api_key,
    }
    url = f"{API_BASE}/details/json?{urllib.parse.urlencode(params)}"
    data = http_get_json(url)
    status = data.get("status")
    if status != "OK":
        raise RuntimeError(f"Place Details failed: {status} {data.get('error_message', '')}")
    return data.get("result", {})


def normalize_url(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if not url.startswith(("http://", "https://")):
        return "https://" + url
    return url


def same_domain(url: str, candidate: str) -> bool:
    try:
        base_host = urllib.parse.urlparse(url).netloc.lower().replace("www.", "")
        cand_host = urllib.parse.urlparse(candidate).netloc.lower().replace("www.", "")
    except ValueError:
        return False
    return bool(base_host and cand_host and (base_host == cand_host or cand_host.endswith("." + base_host)))


def extract_links(base_url: str, page_html: str) -> list[str]:
    links = []
    for match in re.finditer(r"""href=["']([^"']+)["']""", page_html, re.I):
        raw = html.unescape(match.group(1)).strip()
        if not raw or raw.startswith(("#", "tel:", "javascript:")):
            continue
        absolute = urllib.parse.urljoin(base_url, raw)
        links.append(absolute)
    return links


def page_has_form(page_html: str) -> bool:
    lower = page_html.lower()
    form_markers = [
        "<form",
        "contact-form-7",
        "mw_wp_form",
        "wpcf7",
        "formrun",
        "google.com/forms",
        "docs.google.com/forms",
        "typeform",
    ]
    return any(marker in lower for marker in form_markers)


def first_real_contact_form(contact_links: list[str]) -> tuple[str, str]:
    for link in contact_links[:5]:
        try:
            page = http_get_text(link)
        except (urllib.error.URLError, TimeoutError, ValueError):
            continue
        if page_has_form(page):
            return link, "問い合わせフォームあり"
    if contact_links:
        return "", "問い合わせページ候補はあるがフォーム未検出"
    return "", ""


def extract_contact_info(website: str) -> dict:
    empty = {
        "contact_email": "",
        "contact_page_url": "",
        "contact_form_url": "",
        "instagram_url": "",
        "line_url": "",
        "contact_status": "not_checked",
        "contact_reason": "",
    }
    if not website:
        return {**empty, "contact_status": "no_website", "contact_reason": "公式サイトなし"}

    website = normalize_url(website)
    try:
        page = http_get_text(website)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return {
            **empty,
            "contact_status": "fetch_failed",
            "contact_reason": f"公式サイト取得失敗: {type(exc).__name__}",
        }

    emails = sorted(set(re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", page)))
    links = extract_links(website, page)
    contact_links = []
    instagram_links = []
    line_links = []

    for link in links:
        lower = urllib.parse.unquote(link).lower()
        if "instagram.com" in lower:
            instagram_links.append(link)
        if "line.me" in lower or "lin.ee" in lower:
            line_links.append(link)
        is_ignored_asset = any(part in lower for part in IGNORE_CONTACT_URL_PARTS)
        if (
            not is_ignored_asset
            and any(hint.lower() in lower for hint in CONTACT_PATH_HINTS)
            and same_domain(website, link)
        ):
            contact_links.append(link)

    contact_links = sorted(set(contact_links))
    form_url, form_reason = first_real_contact_form(contact_links)

    reason_parts = []
    if emails:
        reason_parts.append("メールあり")
    if form_url:
        reason_parts.append(form_reason)
    elif contact_links:
        reason_parts.append("問い合わせページ候補あり")
    if instagram_links:
        reason_parts.append("Instagramあり")
    if line_links:
        reason_parts.append("LINEあり")

    return {
        "contact_email": emails[0] if emails else "",
        "contact_page_url": contact_links[0] if contact_links else "",
        "contact_form_url": form_url,
        "instagram_url": instagram_links[0] if instagram_links else "",
        "line_url": line_links[0] if line_links else "",
        "contact_status": "found" if reason_parts else "not_found",
        "contact_reason": " / ".join(reason_parts) if reason_parts else "トップページから連絡先候補を検出できず",
    }


def detect_chain_like(place: dict) -> tuple[bool, str]:
    name = str(place.get("name") or "")
    website = str(place.get("website") or "")
    url_host = urllib.parse.urlparse(normalize_url(website)).netloc.lower().replace("www.", "")
    reasons = []

    for pattern in CHAIN_NAME_PATTERNS:
        if pattern.lower() in name.lower():
            reasons.append(f"名称に「{pattern}」")

    for pattern in CHAIN_URL_PATTERNS:
        if pattern.lower() in url_host:
            reasons.append(f"URLが既知チェーン候補: {pattern}")

    if re.search(r"[一-龥ぁ-んァ-ヶA-Za-z0-9]+院\s+[一-龥ぁ-んァ-ヶA-Za-z0-9]+院$", name):
        reasons.append("名称が複数店舗/支店名らしい")
    elif re.search(r"(新宿院|北新宿院|渋谷院|横浜院|川崎院|町田院)$", name):
        reasons.append("名称が支店名らしい")

    # Store-front pages under deep paths often belong to multi-location operators.
    parsed = urllib.parse.urlparse(normalize_url(website))
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) >= 2 and any(word in parsed.path.lower() for word in ["shop", "clinic", "shinjuku", "yokohama"]):
        reasons.append("複数店舗サイトの店舗別ページらしい")

    return bool(reasons), " / ".join(reasons)


def score_lead(place: dict, contact: dict, chain_like: bool) -> tuple[int, str]:
    score = 0
    reasons = []
    ratings_total = int(place.get("user_ratings_total") or 0)
    rating = float(place.get("rating") or 0)

    if ratings_total >= 80:
        score += 30
        reasons.append("口コミ80件以上")
    elif ratings_total >= 30:
        score += 22
        reasons.append("口コミ30件以上")
    elif ratings_total >= 10:
        score += 12
        reasons.append("口コミ10件以上")
    else:
        reasons.append("口コミ件数が少ない")

    if 3.6 <= rating <= 4.4:
        score += 25
        reasons.append("改善余地を伝えやすい評価帯")
    elif 4.5 <= rating <= 4.8:
        score += 15
        reasons.append("良い口コミ返信の価値を伝えやすい")
    elif rating and rating < 3.6:
        score += 8
        reasons.append("低評価対応が必要そう")

    if place.get("website"):
        score += 18
        reasons.append("公式サイトあり")
    if place.get("formatted_phone_number") or place.get("international_phone_number"):
        score += 8
        reasons.append("電話番号あり")
    if place.get("business_status") == "OPERATIONAL":
        score += 7
        reasons.append("営業中")

    if contact.get("contact_form_url") or contact.get("contact_email"):
        score += 15
        reasons.append("フォーム/メールあり")
    elif contact.get("instagram_url") or contact.get("line_url"):
        score += 8
        reasons.append("SNS/LINEあり")

    if chain_like:
        score -= 28
        reasons.append("チェーン/複数店舗運営の可能性")

    return max(score, 0), " / ".join(reasons)


def build_outreach_draft(name: str) -> str:
    return (
        f"{name} ご担当者様\n\n"
        "突然のご連絡失礼いたします。\n"
        "Googleマップの口コミページを拝見し、新規のお客様が口コミを見た時の印象づくりについて、"
        "無料で簡単なチェックと返信文サンプルを作成できればと思いご連絡しました。\n\n"
        "内容は、口コミページの見え方チェックと、実際に使える口コミ返信文3件分のサンプルです。\n"
        "営業というより、まずは無料サンプルとして見ていただければと思います。\n\n"
        "必要でしたら、こちらで3件分だけ作成してお送りします。"
    )


def suggested_offer() -> str:
    return "Google口コミの無料チェック + 返信文3件サンプル"


def collect_leads(
    api_key: str,
    areas: list[str],
    keyword: str,
    limit: int,
    sleep_sec: float,
    enrich_contacts: bool,
) -> list[dict]:
    rows = []
    seen_place_ids = set()
    for area in areas:
        query = f"{area} {keyword}".strip()
        results = call_places_text_search(api_key, query)
        for item in results[:limit]:
            place_id = item.get("place_id")
            if not place_id or place_id in seen_place_ids:
                continue
            seen_place_ids.add(place_id)
            details = call_place_details(api_key, place_id)
            chain_like, chain_reason = detect_chain_like(details)
            contact = extract_contact_info(details.get("website", "")) if enrich_contacts else {
                "contact_email": "",
                "contact_form_url": "",
                "instagram_url": "",
                "line_url": "",
                "contact_status": "skipped",
                "contact_reason": "問い合わせ先抽出をスキップ",
            }
            score, reason = score_lead(details, contact, chain_like)
            name = details.get("name", "")
            rows.append(
                {
                    "area": area,
                    "query": query,
                    "name": name,
                    "address": details.get("formatted_address", ""),
                    "rating": details.get("rating", ""),
                    "user_ratings_total": details.get("user_ratings_total", ""),
                    "website": details.get("website", ""),
                    "phone": details.get("formatted_phone_number")
                    or details.get("international_phone_number", ""),
                    "google_maps_url": details.get("url", ""),
                    "contact_email": contact.get("contact_email", ""),
                    "contact_page_url": contact.get("contact_page_url", ""),
                    "contact_form_url": contact.get("contact_form_url", ""),
                    "instagram_url": contact.get("instagram_url", ""),
                    "line_url": contact.get("line_url", ""),
                    "contact_status": contact.get("contact_status", ""),
                    "contact_reason": contact.get("contact_reason", ""),
                    "chain_like": "yes" if chain_like else "no",
                    "chain_reason": chain_reason,
                    "score": score,
                    "score_reason": reason,
                    "suggested_offer": suggested_offer(),
                    "outreach_draft": build_outreach_draft(name),
                    "確認ステータス": "未確認",
                    "直近口コミ": "",
                    "返信状況": "",
                    "低評価状況": "",
                    "営業実在": "",
                    "連絡導線": "",
                    "判断": "",
                    "メモ": "",
                    "next_action": "手動確認" if not chain_like else "後回し",
                }
            )
            time.sleep(sleep_sec)
    rows.sort(key=lambda row: int(row["score"]), reverse=True)
    return rows


def write_csv(rows: list[dict]) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = OUTPUT_DIR / f"leads_{timestamp}.csv"
    fields = [
        "area",
        "query",
        "name",
        "address",
        "rating",
        "user_ratings_total",
        "website",
        "phone",
        "google_maps_url",
        "contact_email",
        "contact_page_url",
        "contact_form_url",
        "instagram_url",
        "line_url",
        "contact_status",
        "contact_reason",
        "chain_like",
        "chain_reason",
        "score",
        "score_reason",
        "suggested_offer",
        "outreach_draft",
        "確認ステータス",
        "直近口コミ",
        "返信状況",
        "低評価状況",
        "営業実在",
        "連絡導線",
        "判断",
        "メモ",
        "next_action",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Google口コミ・印象改善サポート用のリード候補を抽出します。")
    parser.add_argument("--areas", required=True, help='カンマ区切りの地域。例: "新宿区,渋谷区,横浜市"')
    parser.add_argument("--keyword", default="整体 接骨院", help='検索キーワード。例: "整体 接骨院"')
    parser.add_argument("--limit", type=int, default=20, help="地域ごとの最大取得件数")
    parser.add_argument("--sleep", type=float, default=0.15, help="API呼び出し間の待機秒数")
    parser.add_argument(
        "--skip-contact-enrichment",
        action="store_true",
        help="公式サイトからの問い合わせ先抽出をスキップします。",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        raise SystemExit("GOOGLE_MAPS_API_KEY が未設定です。PowerShellで $env:GOOGLE_MAPS_API_KEY='...' を設定してください。")
    areas = [area.strip() for area in args.areas.split(",") if area.strip()]
    if not areas:
        raise SystemExit("--areas に地域を指定してください。")
    rows = collect_leads(
        api_key=api_key,
        areas=areas,
        keyword=args.keyword,
        limit=args.limit,
        sleep_sec=args.sleep,
        enrich_contacts=not args.skip_contact_enrichment,
    )
    path = write_csv(rows)
    print(f"wrote {len(rows)} leads: {path}")


if __name__ == "__main__":
    main()
