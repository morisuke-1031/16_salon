from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple


DEFAULT_FIELD_MASK = ",".join(
    [
        "id",
        "displayName",
        "formattedAddress",
        "nationalPhoneNumber",
        "websiteUri",
        "googleMapsUri",
        "rating",
        "userRatingCount",
        "businessStatus",
        "primaryType",
        "types",
    ]
)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_yyyy_mm(s: str) -> str:
    parts = s.strip().split("-")
    if len(parts) != 2:
        raise ValueError(f"Invalid month (expected YYYY-MM): {s!r}")
    y, m = parts
    if not (len(y) == 4 and len(m) == 2 and y.isdigit() and m.isdigit()):
        raise ValueError(f"Invalid month (expected YYYY-MM): {s!r}")
    datetime(int(y), int(m), 1)
    return f"{y}-{m}"


def load_env_key() -> str:
    key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if key:
        return key

    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            if k.strip() == "GOOGLE_MAPS_API_KEY":
                key = v.strip().strip('"').strip("'")
                if key:
                    return key

    raise RuntimeError("GOOGLE_MAPS_API_KEY not found. Set env var or .env with GOOGLE_MAPS_API_KEY=...")


def load_master(path: Path) -> Dict[str, Any]:
    raw = path.read_text(encoding="utf-8-sig").strip() if path.exists() else ""
    if not raw:
        raise FileNotFoundError(f"master.json not found or empty: {path}")
    obj = json.loads(raw)
    if not isinstance(obj, dict) or not isinstance(obj.get("places"), dict):
        raise ValueError("Invalid master.json format")
    return obj


def save_master(path: Path, master: Dict[str, Any]) -> None:
    if "meta" not in master or not isinstance(master["meta"], dict):
        master["meta"] = {}
    master["meta"]["updated_at"] = now_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(master, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_place_shape(rec: Dict[str, Any]) -> None:
    if "details_min" not in rec:
        rec["details_min"] = None
    if "details_fetched_at" not in rec:
        rec["details_fetched_at"] = None


def fetch_place_details(
    api_key: str,
    place_id: str,
    field_mask: str,
    language_code: str = "ja",
    region_code: str = "JP",
    timeout: int = 30,
) -> Dict[str, Any]:
    quoted_id = urllib.parse.quote(place_id, safe="")
    query = urllib.parse.urlencode({"languageCode": language_code, "regionCode": region_code})
    url = f"https://places.googleapis.com/v1/places/{quoted_id}?{query}"
    req = urllib.request.Request(url, method="GET")
    req.add_header("X-Goog-Api-Key", api_key)
    req.add_header("X-Goog-FieldMask", field_mask)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        msg = body.strip() or str(e)
        raise RuntimeError(f"HTTPError {e.code}: {msg}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"URLError: {e}") from e


def to_details_min(obj: Dict[str, Any]) -> Dict[str, Any]:
    display_name = obj.get("displayName") or {}
    if not isinstance(display_name, dict):
        display_name = {}
    return {
        "place_id": str(obj.get("id", "")).strip() or None,
        "name": str(display_name.get("text", "")).strip() or None,
        "formatted_address": str(obj.get("formattedAddress", "")).strip() or None,
        "phone": str(obj.get("nationalPhoneNumber", "")).strip() or None,
        "website": str(obj.get("websiteUri", "")).strip() or None,
        "google_maps_uri": str(obj.get("googleMapsUri", "")).strip() or None,
        "rating": obj.get("rating"),
        "user_rating_count": obj.get("userRatingCount"),
        "business_status": str(obj.get("businessStatus", "")).strip() or None,
        "primary_type": str(obj.get("primaryType", "")).strip() or None,
        "types": obj.get("types") if isinstance(obj.get("types"), list) else [],
    }


def collect_targets(
    places: Dict[str, Any],
    month: str,
    force: bool,
) -> List[Tuple[str, Dict[str, Any]]]:
    prefix = month + "-"
    targets: List[Tuple[str, Dict[str, Any]]] = []
    for pid, rec_any in places.items():
        if not isinstance(rec_any, dict):
            continue
        rec = rec_any
        ensure_place_shape(rec)
        first_seen = str(rec.get("first_seen", ""))
        if not first_seen.startswith(prefix):
            continue
        if (not force) and rec.get("details_fetched_at"):
            continue
        targets.append((str(pid), rec))
    return targets


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch Place Details for monthly new places and update master.json.")
    ap.add_argument("--month", required=True, help="Target month (YYYY-MM), e.g. 2026-03")
    ap.add_argument("--master", default="data/master.json", help="Path to master.json")
    ap.add_argument("--field-mask", default=DEFAULT_FIELD_MASK, help="X-Goog-FieldMask value for Details API.")
    ap.add_argument("--language-code", default="ja", help="Details languageCode.")
    ap.add_argument("--region-code", default="JP", help="Details regionCode.")
    ap.add_argument("--limit", type=int, default=0, help="Max records to fetch (0 = no limit).")
    ap.add_argument("--sleep", type=float, default=0.2, help="Sleep seconds between API calls.")
    ap.add_argument("--timeout", type=int, default=30, help="HTTP timeout seconds.")
    ap.add_argument("--save-every", type=int, default=50, help="Save master every N successful fetches.")
    ap.add_argument("--force", action="store_true", help="Fetch even if details_fetched_at already exists.")
    args = ap.parse_args()

    month = normalize_yyyy_mm(args.month)
    api_key = load_env_key()
    master_path = Path(args.master)
    master = load_master(master_path)
    places: Dict[str, Any] = master["places"]

    targets = collect_targets(places=places, month=month, force=args.force)
    if args.limit > 0:
        targets = targets[: args.limit]

    print(f"TARGETS: month={month} count={len(targets)} force={args.force}")
    if not targets:
        print("Nothing to fetch.")
        return 0

    ok = 0
    fail = 0
    start = now_iso()

    for i, (pid, rec) in enumerate(targets, start=1):
        try:
            obj = fetch_place_details(
                api_key=api_key,
                place_id=pid,
                field_mask=args.field_mask,
                language_code=args.language_code,
                region_code=args.region_code,
                timeout=args.timeout,
            )
            rec["details_min"] = to_details_min(obj)
            rec["details_fetched_at"] = now_iso()
            rec["details_error"] = None
            rec["details_error_at"] = None
            ok += 1
        except Exception as e:
            fail += 1
            rec["details_error"] = str(e)[:500]
            rec["details_error_at"] = now_iso()

        if i % 20 == 0 or fail > 0:
            print(f"[{i}/{len(targets)}] ok={ok} fail={fail}")

        if args.save_every > 0 and ok > 0 and ok % args.save_every == 0:
            save_master(master_path, master)
            print(f"Saved checkpoint: ok={ok} fail={fail}")

        time.sleep(max(0.0, args.sleep))

    save_master(master_path, master)

    end = now_iso()
    print("OK:")
    print(f"  master={master_path}")
    print(f"  month={month}")
    print(f"  targets={len(targets)} ok={ok} fail={fail}")
    print(f"  started_at={start}")
    print(f"  ended_at={end}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
