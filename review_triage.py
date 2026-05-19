import argparse
import csv
import datetime as dt
import html
import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


FIELD_ALIASES = {
    "確認ステータス": ["manual_check_status"],
    "直近口コミ": ["manual_recent_reviews"],
    "返信状況": ["manual_reply_status"],
    "低評価状況": ["manual_low_rating_status"],
    "営業実在": ["manual_business_alive"],
    "連絡導線": ["manual_contact_ok"],
    "判断": ["manual_decision"],
    "メモ": ["manual_notes"],
}

MANUAL_FIELDS = list(FIELD_ALIASES.keys())

FIELD_OPTIONS = {
    "確認ステータス": ["未確認", "確認済み"],
    "直近口コミ": ["", "あり", "古い", "不明"],
    "返信状況": ["", "未返信多い", "一部未返信", "全返信", "不明"],
    "低評価状況": ["", "放置あり", "なし", "不明"],
    "営業実在": ["", "あり", "不明", "怪しい"],
    "連絡導線": ["", "フォーム", "メール", "Instagram", "LINE", "電話のみ", "なし"],
    "判断": ["", "送信A", "送信B", "保留", "除外"],
}

DECISIONS = ["送信A", "送信B", "保留", "除外"]
DECISION_LABELS = {
    "送信A": "送信A（かなり送ってよさそう）",
    "送信B": "送信B（再度内容確認）",
    "保留": "保留",
    "除外": "除外",
    "": "未選択",
}


def migrate_manual_fields(fields: list[str], rows: list[dict]) -> list[str]:
    for row in rows:
        for jp_field, aliases in FIELD_ALIASES.items():
            if jp_field not in row:
                row[jp_field] = ""
            if not row[jp_field]:
                for alias in aliases:
                    if row.get(alias):
                        row[jp_field] = row.get(alias, "")
                        break

    for jp_field in MANUAL_FIELDS:
        if jp_field not in fields:
            fields.append(jp_field)

    # Keep old English columns if they exist, but prefer Japanese columns in the UI.
    return fields


def read_csv(path: Path) -> tuple[list[str], list[dict]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fields = list(reader.fieldnames or [])
    fields = migrate_manual_fields(fields, rows)
    return fields, rows


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    try:
        os.replace(tmp, path)
    except PermissionError:
        fallback = path.with_name(f"{path.stem}_reviewed{path.suffix}")
        os.replace(tmp, fallback)
        print(f"CSV is open or locked. Saved fallback: {fallback}")


def value(row: dict, key: str) -> str:
    return str(row.get(key) or "")


def row_summary(row: dict) -> dict:
    keys = [
        "name",
        "rating",
        "user_ratings_total",
        "address",
        "website",
        "phone",
        "google_maps_url",
        "contact_page_url",
        "contact_form_url",
        "contact_email",
        "instagram_url",
        "line_url",
        "chain_like",
        "chain_reason",
        "score",
        "score_reason",
        *MANUAL_FIELDS,
    ]
    return {key: value(row, key) for key in keys}


def html_escape(text: str) -> str:
    return html.escape(str(text or ""), quote=True)


def link(label: str, url: str) -> str:
    if not url:
        return '<span class="muted">なし</span>'
    safe_url = html_escape(url)
    return f'<a href="{safe_url}" target="_blank" rel="noreferrer">{html_escape(label)}</a>'


def option_buttons(field: str, current: str) -> str:
    buttons = []
    for option in FIELD_OPTIONS[field]:
        label = DECISION_LABELS.get(option, option or "未選択") if field == "判断" else option or "未選択"
        selected = " selected" if option == current else ""
        buttons.append(
            f'<button type="button" class="choice{selected}" data-field="{html_escape(field)}" data-value="{html_escape(option)}">{html_escape(label)}</button>'
        )
    return "\n".join(buttons)


class TriageApp:
    def __init__(self, csv_path: Path):
        self.csv_path = csv_path
        self.fields, self.rows = read_csv(csv_path)
        self.lock = threading.Lock()
        write_csv(self.csv_path, self.fields, self.rows)

    def save_row(self, index: int, updates: dict) -> None:
        with self.lock:
            row = self.rows[index]
            for key, val in updates.items():
                if key in MANUAL_FIELDS:
                    row[key] = val
            if updates:
                row["確認ステータス"] = "確認済み"
            write_csv(self.csv_path, self.fields, self.rows)

    def append_note(self, index: int, note: str, decision: str = "") -> None:
        with self.lock:
            row = self.rows[index]
            today = dt.datetime.now().strftime("%Y-%m-%d")
            current = value(row, "メモ").strip()
            addition = f"{today} {note}"
            row["メモ"] = f"{current}\n{addition}".strip() if current else addition
            row["確認ステータス"] = "確認済み"
            if decision:
                row["判断"] = decision
            write_csv(self.csv_path, self.fields, self.rows)

    def stats(self) -> dict:
        total = len(self.rows)
        decisions = {"送信A": 0, "送信B": 0, "保留": 0, "除外": 0, "未判断": 0}
        for row in self.rows:
            decision = value(row, "判断")
            if decision in DECISIONS:
                decisions[decision] += 1
            else:
                decisions["未判断"] += 1
        return {"total": total, **decisions}

    def sendable_rows(self) -> list[tuple[int, dict]]:
        return [
            (idx, row)
            for idx, row in enumerate(self.rows)
            if value(row, "判断") in {"送信A", "送信B", "送信"}
        ]

    def render(self, index: int) -> str:
        if not self.rows:
            return self.layout("<main><h1>No rows</h1><p>CSVに行がありません。</p></main>")

        index = max(0, min(index, len(self.rows) - 1))
        row = self.rows[index]
        data = row_summary(row)
        stats = self.stats()
        prev_index = max(0, index - 1)
        next_index = min(len(self.rows) - 1, index + 1)

        fields_html = "\n".join(
            f"""
            <section class="field-group">
              <div class="field-title">{html_escape(field)}</div>
              <div class="choices">{option_buttons(field, data[field])}</div>
            </section>
            """
            for field in MANUAL_FIELDS
            if field != "メモ"
        )

        state = {f: data[f] for f in MANUAL_FIELDS}
        fields_json = json.dumps([f for f in MANUAL_FIELDS if f != "メモ"], ensure_ascii=False)
        state_json = json.dumps(state, ensure_ascii=False)
        sendable_html = self.render_sendable_table()

        body = f"""
        <main>
          <header class="topbar">
            <div>
              <div class="eyebrow">Review triage</div>
              <h1>{html_escape(data["name"])}</h1>
              <p class="muted">{index + 1} / {len(self.rows)} | score {html_escape(data["score"])} | chain_like: {html_escape(data["chain_like"])}</p>
            </div>
            <nav class="nav">
              <a class="btn" href="/?i={prev_index}">前へ</a>
              <a class="btn primary" href="/?i={next_index}">次へ</a>
              <button type="button" id="top-save" class="primary">保存</button>
            </nav>
          </header>

          <section class="stats">
            <span>total {stats["total"]}</span>
            <span>送信A {stats["送信A"]}</span>
            <span>送信B {stats["送信B"]}</span>
            <span>保留 {stats["保留"]}</span>
            <span>除外 {stats["除外"]}</span>
            <span>未判断 {stats["未判断"]}</span>
          </section>

          <section class="grid">
            <article class="panel">
              <h2>店舗情報</h2>
              <dl>
                <dt>評価</dt><dd>{html_escape(data["rating"])} ({html_escape(data["user_ratings_total"])}件)</dd>
                <dt>住所</dt><dd>{html_escape(data["address"])}</dd>
                <dt>電話</dt><dd>{html_escape(data["phone"]) or '<span class="muted">なし</span>'}</dd>
                <dt>公式サイト</dt><dd>{link("開く", data["website"])}</dd>
                <dt>Google Maps</dt><dd>{link("口コミを確認", data["google_maps_url"])}</dd>
                <dt>問い合わせページ</dt><dd>{link("開く", data["contact_page_url"])}</dd>
                <dt>フォーム</dt><dd>{link("開く", data["contact_form_url"])}</dd>
                <dt>メール</dt><dd>{html_escape(data["contact_email"]) or '<span class="muted">なし</span>'}</dd>
                <dt>Instagram</dt><dd>{link("開く", data["instagram_url"])}</dd>
                <dt>LINE</dt><dd>{link("開く", data["line_url"])}</dd>
              </dl>
              <h3>自動判定</h3>
              <p><b>chain_reason:</b> {html_escape(data["chain_reason"]) or '<span class="muted">なし</span>'}</p>
              <p><b>score_reason:</b> {html_escape(data["score_reason"])}</p>
            </article>

            <article class="panel">
              <h2>手動確認</h2>
              <form id="triage-form">
                {fields_html}
                <section class="field-group">
                  <div class="field-title">メモ</div>
                  <textarea id="notes" rows="5">{html_escape(data["メモ"])}</textarea>
                </section>
                <div class="actions">
                  <button type="button" class="decision send" data-decision="送信A">送信A</button>
                  <button type="button" class="decision send-b" data-decision="送信B">送信B</button>
                  <button type="button" class="decision hold" data-decision="保留">保留</button>
                  <button type="button" class="decision exclude" data-decision="除外">除外</button>
                </div>
                <div class="quick-actions">
                  <button type="button" class="quick" data-note="フォーム送信済み">フォーム送信済み</button>
                  <button type="button" class="quick" data-note="営業NG表示ありのため見送り" data-decision="除外">営業NG見送り</button>
                  <button type="button" class="quick" data-note="返信あり">返信あり</button>
                  <button type="button" class="quick" data-note="無料チェック希望あり" data-decision="送信A">無料チェック希望</button>
                </div>
              </form>
              <p id="status" class="muted"></p>
            </article>
          </section>

          {sendable_html}

          <section class="panel checklist">
            <h2>確認ポイント</h2>
            <ul>
              <li>直近1年以内に口コミがあるか</li>
              <li>直近10件で未返信が3件以上あるか</li>
              <li>星1〜3の口コミが放置されているか</li>
              <li>営業実在があり、フォームまたはメールで連絡できるか</li>
              <li>チェーン/複数店舗運営っぽくないか</li>
            </ul>
          </section>
        </main>
        <script>
          const index = {index};
          const fields = {fields_json};
          const state = {state_json};

          function selectChoice(field, value) {{
            state[field] = value;
            document.querySelectorAll(`[data-field="${{field}}"]`).forEach(btn => {{
              btn.classList.toggle("selected", btn.dataset.value === value);
            }});
          }}

          document.querySelectorAll(".choice").forEach(btn => {{
            btn.addEventListener("click", () => selectChoice(btn.dataset.field, btn.dataset.value));
          }});

          document.querySelectorAll(".decision").forEach(btn => {{
            btn.addEventListener("click", () => {{
              selectChoice("判断", btn.dataset.decision);
              save(true);
            }});
          }});

          document.getElementById("top-save").addEventListener("click", () => save(false));

          document.querySelectorAll(".quick").forEach(btn => {{
            btn.addEventListener("click", async () => {{
              const response = await fetch(`/api/append-note?i=${{index}}`, {{
                method: "POST",
                headers: {{ "Content-Type": "application/json" }},
                body: JSON.stringify({{ note: btn.dataset.note, decision: btn.dataset.decision || "" }})
              }});
              if (!response.ok) {{
                document.getElementById("status").textContent = "追記に失敗しました";
                return;
              }}
              document.getElementById("status").textContent = "メモに追記しました";
              window.location.reload();
            }});
          }});

          async function save(goNext) {{
            state["メモ"] = document.getElementById("notes").value;
            state["確認ステータス"] = "確認済み";
            const response = await fetch(`/api/save?i=${{index}}`, {{
              method: "POST",
              headers: {{ "Content-Type": "application/json" }},
              body: JSON.stringify(state)
            }});
            if (!response.ok) {{
              document.getElementById("status").textContent = "保存に失敗しました";
              return;
            }}
            document.getElementById("status").textContent = "保存しました";
            if (goNext) {{
              window.location.href = "/?i=" + Math.min(index + 1, {len(self.rows) - 1});
            }}
          }}
        </script>
        """
        return self.layout(body)

    def render_sendable_table(self) -> str:
        rows = self.sendable_rows()
        if not rows:
            return """
            <section class="panel sendable">
              <h2>送信候補一覧</h2>
              <p class="muted">まだ送信A/送信Bの店舗はありません。</p>
            </section>
            """

        body_rows = []
        for idx, row in rows:
            decision = html_escape(value(row, "判断"))
            name = html_escape(value(row, "name"))
            rating = html_escape(value(row, "rating"))
            count = html_escape(value(row, "user_ratings_total"))
            contact = (
                value(row, "contact_form_url")
                or value(row, "contact_email")
                or value(row, "instagram_url")
                or value(row, "line_url")
            )
            contact_html = link("開く", contact) if contact.startswith(("http://", "https://")) else html_escape(contact or "なし")
            body_rows.append(
                f"""
                <tr>
                  <td><span class="badge">{decision}</span></td>
                  <td><a href="/?i={idx}">{name}</a></td>
                  <td>{rating} ({count}件)</td>
                  <td>{contact_html}</td>
                </tr>
                """
            )

        return f"""
        <section class="panel sendable">
          <h2>送信候補一覧</h2>
          <table class="sendable-table">
            <thead>
              <tr><th>判断</th><th>店舗</th><th>評価</th><th>連絡先</th></tr>
            </thead>
            <tbody>
              {''.join(body_rows)}
            </tbody>
          </table>
        </section>
        """

    def layout(self, body: str) -> str:
        return f"""<!doctype html>
        <html lang="ja">
        <head>
          <meta charset="utf-8">
          <meta name="viewport" content="width=device-width, initial-scale=1">
          <title>Review triage</title>
          <style>
            body {{
              margin: 0;
              font-family: "Yu Gothic", "Meiryo", system-ui, sans-serif;
              background: #f6f7f9;
              color: #172033;
            }}
            main {{ max-width: 1180px; margin: 0 auto; padding: 24px; }}
            .topbar {{ display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 14px; }}
            .eyebrow {{ color: #57677f; font-size: 12px; font-weight: 700; text-transform: uppercase; }}
            h1 {{ margin: 4px 0 6px; font-size: 28px; line-height: 1.25; }}
            h2 {{ font-size: 17px; margin: 0 0 12px; }}
            h3 {{ font-size: 14px; margin: 18px 0 8px; }}
            .muted {{ color: #657389; }}
            .grid {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(360px, 0.8fr); gap: 16px; }}
            .panel {{ background: #fff; border: 1px solid #dbe2ea; border-radius: 8px; padding: 18px; box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04); }}
            .stats {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }}
            .stats span {{ background: #e9eef5; border-radius: 999px; padding: 5px 10px; font-size: 12px; }}
            dl {{ display: grid; grid-template-columns: 130px minmax(0, 1fr); gap: 8px 12px; margin: 0; }}
            dt {{ color: #57677f; font-weight: 700; }}
            dd {{ margin: 0; overflow-wrap: anywhere; }}
            a {{ color: #0b57d0; text-decoration: none; font-weight: 700; }}
            .nav, .actions {{ display: flex; gap: 8px; flex-wrap: wrap; }}
            .quick-actions {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; padding-top: 12px; border-top: 1px solid var(--line); }}
            .btn, button {{ border: 1px solid #c8d2df; border-radius: 7px; background: #fff; padding: 8px 12px; cursor: pointer; font-weight: 700; color: #172033; }}
            .btn.primary, button.primary {{ background: #0b57d0; border-color: #0b57d0; color: #fff; }}
            .field-group {{ margin-bottom: 13px; }}
            .field-title {{ font-size: 12px; color: #57677f; font-weight: 700; margin-bottom: 6px; }}
            .choices {{ display: flex; gap: 6px; flex-wrap: wrap; }}
            .choice.selected {{ background: #17324d; border-color: #17324d; color: #fff; }}
            .decision.send {{ background: #e7f5ec; border-color: #9bd3af; }}
            .decision.send-b {{ background: #eef7ff; border-color: #8bbfe8; }}
            .decision.hold {{ background: #fff6df; border-color: #e3c268; }}
            .decision.exclude {{ background: #fdecec; border-color: #e6a0a0; }}
            .quick {{ background: #f7f4ef; border-color: #ded8cf; }}
            textarea {{ width: 100%; box-sizing: border-box; border: 1px solid #c8d2df; border-radius: 7px; padding: 10px; font-family: inherit; font-size: 14px; }}
            .checklist {{ margin-top: 16px; }}
            .checklist li {{ margin: 5px 0; }}
            .sendable {{ margin-top: 16px; }}
            .sendable-table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
            .sendable-table th, .sendable-table td {{ border-bottom: 1px solid #dbe2ea; padding: 9px 8px; text-align: left; vertical-align: top; }}
            .sendable-table th {{ color: #57677f; font-size: 12px; }}
            .badge {{ display: inline-block; padding: 3px 8px; border-radius: 999px; background: #e9eef5; font-weight: 700; }}
            @media (max-width: 860px) {{
              .grid {{ grid-template-columns: 1fr; }}
              .topbar {{ flex-direction: column; }}
              dl {{ grid-template-columns: 1fr; }}
            }}
          </style>
        </head>
        <body>{body}</body>
        </html>"""


def make_handler(app: TriageApp):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path != "/":
                self.send_error(404)
                return
            params = parse_qs(parsed.query)
            index = int(params.get("i", ["0"])[0] or 0)
            content = app.render(index).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_POST(self):
            parsed = urlparse(self.path)
            if parsed.path not in {"/api/save", "/api/append-note"}:
                self.send_error(404)
                return
            params = parse_qs(parsed.query)
            index = int(params.get("i", ["0"])[0] or 0)
            if index < 0 or index >= len(app.rows):
                self.send_error(400)
                return
            length = int(self.headers.get("Content-Length", "0"))
            payload = self.rfile.read(length).decode("utf-8")
            updates = json.loads(payload or "{}")
            if parsed.path == "/api/save":
                app.save_row(index, updates)
            else:
                app.append_note(index, updates.get("note", ""), updates.get("decision", ""))
            content = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, fmt, *args):
            return

    return Handler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CSVの店舗候補を人間が高速確認するためのローカルUIです。")
    parser.add_argument("csv_path", help="lead_finder.py が出力したCSVパス")
    parser.add_argument("--port", type=int, default=8765, help="ローカルUIのポート")
    parser.add_argument("--no-open", action="store_true", help="ブラウザを自動で開かない")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    csv_path = Path(args.csv_path)
    if not csv_path.exists():
        raise SystemExit(f"CSVが見つかりません: {csv_path}")
    app = TriageApp(csv_path)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app))
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Review triage UI: {url}")
    print(f"CSV: {csv_path}")
    print("Stop: Ctrl+C")
    if not args.no_open:
        webbrowser.open(url)
    server.serve_forever()


if __name__ == "__main__":
    main()
