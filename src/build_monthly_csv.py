from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple


def normalize_yyyy_mm(s: str) -> str:
    parts = s.strip().split("-")
    if len(parts) != 2:
        raise ValueError(f"Invalid month (expected YYYY-MM): {s!r}")
    y, m = parts
    if not (len(y) == 4 and len(m) == 2 and y.isdigit() and m.isdigit()):
        raise ValueError(f"Invalid month (expected YYYY-MM): {s!r}")
    datetime(int(y), int(m), 1)
    return f"{y}-{m}"


def load_master(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"master.json not found: {path}")
    raw = path.read_text(encoding="utf-8-sig").strip()
    if not raw:
        raise ValueError(f"master.json is empty: {path}")
    obj = json.loads(raw)
    if not isinstance(obj, dict) or not isinstance(obj.get("places"), dict):
        raise ValueError("Invalid master.json format")
    return obj


def parse_ward(formatted_address: str) -> str:
    m = re.search(r"東京都([^0-9\s]+?区)", formatted_address or "")
    return m.group(1) if m else ""


def to_float(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except Exception:
        return None


def to_int(v: Any) -> int | None:
    try:
        if v is None or v == "":
            return None
        return int(v)
    except Exception:
        return None


def to_str(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() == "none" else s


def to_excel_hyperlink_formula(url: str, label: str, as_plain_url: bool) -> str:
    # Keep this switchable: if formula behavior is not desired, return plain URL.
    if as_plain_url:
        return url
    if not url:
        return ""
    escaped = url.replace('"', '""')
    escaped_label = label.replace('"', '""')
    return f'=HYPERLINK("{escaped}","{escaped_label}")'


def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def score_row(days_since_detected: int, rating: float | None, review_count: int | None) -> float:
    freshness = clamp(50.0 * (1.0 - (days_since_detected / 30.0)), 0.0, 50.0)
    rc = 0 if review_count is None else max(0, review_count)
    review_score = clamp((rc / 50.0) * 30.0, 0.0, 30.0)
    if rating is None:
        rating_score = 0.0
    else:
        rating_score = clamp(((rating - 3.5) / (4.8 - 3.5)) * 20.0, 0.0, 20.0)
    return round(freshness + review_score + rating_score, 2)


def priority_from(score: float, days_since_detected: int, rating: float | None, review_count: int | None) -> str:
    rc = 0 if review_count is None else review_count
    rt = 0.0 if rating is None else rating
    if days_since_detected <= 14 and rc >= 8 and rt >= 4.0:
        return "A"
    if days_since_detected <= 30 and (rc >= 3 or rt >= 3.8):
        return "B"
    return "C"


def main() -> int:
    ap = argparse.ArgumentParser(description="Build monthly sales-ready CSV from master.json.")
    ap.add_argument("--month", required=True, help="YYYY-MM")
    ap.add_argument("--master", default="data/master.json", help="Path to master.json")
    ap.add_argument("--out", default="", help="Output CSV path. Default: out/monthly/YYYY-MM_leads.csv")
    ap.add_argument(
        "--plain-url",
        action="store_true",
        help="Write raw URL text instead of Excel HYPERLINK() formula.",
    )
    ap.add_argument(
        "--as-of",
        default="",
        help="Reference date for scoring (YYYY-MM-DD). Default: today in local timezone.",
    )
    args = ap.parse_args()

    month = normalize_yyyy_mm(args.month)
    master = load_master(Path(args.master))
    places: Dict[str, Any] = master["places"]

    if args.as_of.strip():
        as_of = datetime.strptime(args.as_of.strip(), "%Y-%m-%d").date()
    else:
        as_of = datetime.now().astimezone().date()

    rows: List[Dict[str, Any]] = []
    prefix = month + "-"
    for pid, rec_any in places.items():
        if not isinstance(rec_any, dict):
            continue
        rec = rec_any
        first_seen = str(rec.get("first_seen", ""))
        if not first_seen.startswith(prefix):
            continue
        details = rec.get("details_min")
        if not isinstance(details, dict):
            continue

        name = to_str(details.get("name"))
        formatted_address = to_str(details.get("formatted_address"))
        ward = parse_ward(formatted_address)
        rating = to_float(details.get("rating"))
        user_rating_count = to_int(details.get("user_rating_count"))
        phone = to_str(details.get("phone"))
        website = to_str(details.get("website"))
        google_maps_uri = to_str(details.get("google_maps_uri"))
        business_status = to_str(details.get("business_status"))

        detected_date = datetime.strptime(first_seen, "%Y-%m-%d").date()
        days_since = max(0, (as_of - detected_date).days)
        score = score_row(days_since, rating, user_rating_count)
        priority = priority_from(score, days_since, rating, user_rating_count)
        if business_status and business_status != "OPERATIONAL":
            priority = "C"

        rows.append(
            {
                "priority": priority,
                "score": score,
                "detected_first": first_seen,
                "place_id": str(pid),
                "name": name,
                "formatted_address": formatted_address,
                "ward": ward,
                "rating": rating if rating is not None else "",
                "user_rating_count": user_rating_count if user_rating_count is not None else "",
                "phone": phone,
                "website": to_excel_hyperlink_formula(website, label="サイトへ", as_plain_url=args.plain_url),
                "business_status": business_status,
                "google_maps_uri": to_excel_hyperlink_formula(
                    google_maps_uri, label="Google Mapへ", as_plain_url=args.plain_url
                ),
                "_sort_pid": str(pid),
            }
        )

    def sort_key(r: Dict[str, Any]) -> Tuple[int, float, str]:
        pri_rank = {"A": 0, "B": 1, "C": 2}.get(str(r["priority"]), 9)
        return (pri_rank, -float(r["score"]), str(r["_sort_pid"]))

    rows.sort(key=sort_key)

    out_path = Path(args.out) if args.out.strip() else Path("out") / "monthly" / f"{month}_leads.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fields = [
        "priority",
        "score",
        "detected_first",
        "place_id",
        "name",
        "formatted_address",
        "ward",
        "rating",
        "user_rating_count",
        "phone",
        "website",
        "business_status",
        "google_maps_uri",
    ]

    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            out = {k: r[k] for k in fields}
            w.writerow(out)

    print(f"OK: {out_path} (count={len(rows)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
