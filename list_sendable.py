import argparse
import csv
from pathlib import Path


SENDABLE = {"送信A", "送信B", "送信"}


def main() -> None:
    parser = argparse.ArgumentParser(description="CSVから送信候補だけを表示します。")
    parser.add_argument("csv_path")
    parser.add_argument("--out", default="", help="送信候補だけのCSVを書き出すパス")
    args = parser.parse_args()

    path = Path(args.csv_path)
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fields = reader.fieldnames or []

    sendable = [row for row in rows if (row.get("判断") or row.get("manual_decision") or "") in SENDABLE]
    if not sendable:
        print("送信候補はありません。")
        return

    for idx, row in enumerate(sendable, 1):
        decision = row.get("判断") or row.get("manual_decision") or ""
        name = row.get("name", "")
        rating = row.get("rating", "")
        count = row.get("user_ratings_total", "")
        contact = row.get("contact_form_url") or row.get("contact_email") or row.get("instagram_url") or row.get("line_url") or ""
        print(f"{idx}. [{decision}] {name} / 評価 {rating} ({count}件)")
        print(f"   contact: {contact}")
        print(f"   maps: {row.get('google_maps_url', '')}")
        print()

    if args.out:
        out_path = Path(args.out)
        with out_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(sendable)
        print(f"wrote {len(sendable)} sendable leads: {out_path}")


if __name__ == "__main__":
    main()
