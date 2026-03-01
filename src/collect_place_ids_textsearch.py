from __future__ import annotations

import argparse
import json
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
import urllib.request
import urllib.error


# =========================
# Config
# =========================

WARD_LIST_23KU = [
    "千代田区", "中央区", "港区", "新宿区", "文京区", "台東区", "墨田区", "江東区",
    "品川区", "目黒区", "大田区", "世田谷区", "渋谷区", "中野区", "杉並区", "豊島区",
    "北区", "荒川区", "板橋区", "練馬区", "足立区", "葛飾区", "江戸川区",
]

DEFAULT_QUERY_TEMPLATE = "{ward} 美容院"

# 東京23区をざっくり覆うbbox（必要なら調整OK）
DEFAULT_BBOX_TOKYO_23KU = (35.53000, 139.57000, 35.83000, 139.92000)  # (min_lat, min_lng, max_lat, max_lng)
DEFAULT_GRID_RADIUS_M = 1600.0
DEFAULT_GRID_STEP_M = 1800.0
DEFAULT_GRID_INCLUDED_TYPES = ["hair_salon"]


# =========================
# Utils
# =========================

def jst_now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def today_yyyy_mm_dd() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


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


def write_lines(path: Path, lines: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines) + ("\n" if lines else "")
    path.write_text(text, encoding="utf-8")


def uniq_preserve_order(items: List[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def load_master_place_ids(master_path: Path) -> Set[str]:
    if not master_path.exists():
        return set()
    raw = master_path.read_text(encoding="utf-8-sig").strip()
    if not raw:
        return set()
    obj = json.loads(raw)
    places = obj.get("places", {})
    if not isinstance(places, dict):
        return set()
    return set(places.keys())


def meters_to_lat_deg(m: float) -> float:
    # 1deg lat ≈ 111,320m
    return m / 111320.0


def meters_to_lng_deg(m: float, lat_deg: float) -> float:
    # 1deg lng ≈ 111,320m * cos(lat)
    return m / (111320.0 * max(0.1, math.cos(math.radians(lat_deg))))


# =========================
# HTTP / API
# =========================

def http_post_json(url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: int = 30) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        msg = body.strip() or str(e)
        raise RuntimeError(f"HTTPError {e.code} for {url}: {msg}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"URLError for {url}: {e}") from e


@dataclass
class TextSearchResult:
    place_ids: List[str]
    next_page_token: Optional[str]
    raw_count: int


def text_search_new(
    api_key: str,
    text_query: str,
    language_code: str = "ja",
    region_code: str = "JP",
    page_token: Optional[str] = None,
    location_bias_circle: Optional[Tuple[float, float, float]] = None,  # (lat, lng, radius_m)
    timeout: int = 30,
) -> TextSearchResult:
    """
    Places API (New) Text Search
    POST https://places.googleapis.com/v1/places:searchText

    FieldMask:
      - places.id は place_id 相当
      - nextPageToken は searchText では使える
    """
    url = "https://places.googleapis.com/v1/places:searchText"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.id,nextPageToken",
    }

    payload: Dict[str, Any] = {
        "textQuery": text_query,
        "languageCode": language_code,
        "regionCode": region_code,
    }
    if page_token:
        payload["pageToken"] = page_token
    if location_bias_circle:
        lat, lng, r = location_bias_circle
        payload["locationBias"] = {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": float(r),
            }
        }

    obj = http_post_json(url, headers=headers, payload=payload, timeout=timeout)

    places = obj.get("places", []) or []
    place_ids: List[str] = []
    for p in places:
        pid = str(p.get("id", "")).strip()
        if pid:
            place_ids.append(pid)

    nxt = obj.get("nextPageToken")
    if nxt is not None:
        nxt = str(nxt).strip() or None

    return TextSearchResult(place_ids=place_ids, next_page_token=nxt, raw_count=len(places))


@dataclass
class NearbySearchResult:
    place_ids: List[str]
    raw_count: int


def nearby_search_new(
    api_key: str,
    center_lat: float,
    center_lng: float,
    radius_m: float,
    included_types: List[str],
    language_code: str = "ja",
    region_code: str = "JP",
    timeout: int = 30,
) -> NearbySearchResult:
    """
    Places API (New) Nearby Search
    POST https://places.googleapis.com/v1/places:searchNearby

    重要:
      - FieldMaskに nextPageToken を入れると 400 になるケースがあるため、places.id だけにする
      - pagination は期待しない（必要ならgrid密度でカバーする）
    """
    url = "https://places.googleapis.com/v1/places:searchNearby"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.id",
    }

    payload: Dict[str, Any] = {
        "includedTypes": included_types,
        "languageCode": language_code,
        "regionCode": region_code,
        "locationRestriction": {
            "circle": {
                "center": {"latitude": center_lat, "longitude": center_lng},
                "radius": float(radius_m),
            }
        },
    }

    obj = http_post_json(url, headers=headers, payload=payload, timeout=timeout)

    places = obj.get("places", []) or []
    place_ids: List[str] = []
    for p in places:
        pid = str(p.get("id", "")).strip()
        if pid:
            place_ids.append(pid)

    return NearbySearchResult(place_ids=place_ids, raw_count=len(places))


# =========================
# Main
# =========================

def main() -> int:
    ap = argparse.ArgumentParser(description="Collect place_ids via Places API (New) (IDs-only).")

    ap.add_argument("--mode", choices=["ward", "grid"], default="ward",
                    help="ward: text search by ward query; grid: nearby search by grid coverage")

    # ---- ward mode
    ap.add_argument("--ward", default="", help="Run only one ward (e.g. 千代田区). If empty, uses ward list.")
    ap.add_argument("--ward-limit", type=int, default=0, help="Limit number of wards from list (for test). 0=no limit.")
    ap.add_argument("--query-template", default=DEFAULT_QUERY_TEMPLATE, help="e.g. '{ward} 美容院'")
    ap.add_argument("--max-pages", type=int, default=1, help="How many pages to fetch per ward (>=1). (ward mode only)")

    # ---- common
    ap.add_argument("--sleep", type=float, default=1.2, help="Sleep seconds between requests.")
    ap.add_argument("--page-token-sleep", type=float, default=2.0,
                    help="Extra sleep before using nextPageToken (ward/text mode).")
    ap.add_argument("--hard-cap-ids", type=int, default=3000, help="Stop when total unique ids reaches this cap.")
    ap.add_argument("--inbox-out", default="inbox/place_ids.txt", help="Output path for aggregated place_ids.")
    ap.add_argument("--out-new", default="", help="Output for newly-found ids (diff vs master).")
    ap.add_argument("--master", default="data/master.json", help="Master json path for diff.")

    args = ap.parse_args()

    api_key = load_env_key()
    master_ids = load_master_place_ids(Path(args.master))

    all_ids: List[str] = []
    all_seen: Set[str] = set()

    def add_global(ids: List[str]) -> int:
        add = 0
        for pid in ids:
            if pid in all_seen:
                continue
            all_seen.add(pid)
            all_ids.append(pid)
            add += 1
        return add

    if args.mode == "ward":
        if args.max_pages < 1:
            raise SystemExit("--max-pages must be >= 1")

        # wards
        if args.ward.strip():
            wards = [args.ward.strip()]
        else:
            wards = list(WARD_LIST_23KU)
            if args.ward_limit and args.ward_limit > 0:
                wards = wards[: args.ward_limit]

        for ward in wards:
            q = args.query_template.format(ward=ward)
            ward_ids_raw: List[str] = []
            page_token: Optional[str] = None

            for page in range(1, args.max_pages + 1):
                res = text_search_new(
                    api_key=api_key,
                    text_query=q,
                    language_code="ja",
                    region_code="JP",
                    page_token=page_token,
                )
                ward_ids_raw.extend(res.place_ids)

                page_token = res.next_page_token
                if not page_token:
                    break

                time.sleep(max(args.page_token_sleep, args.sleep))

            ward_uniq = uniq_preserve_order(ward_ids_raw)
            added = add_global(ward_uniq)
            print(f"[{ward}] ids={len(ward_uniq)} added={added} total_so_far={len(all_ids)}")

            if len(all_ids) >= args.hard_cap_ids:
                print(f"STOP: reached hard cap ids={args.hard_cap_ids}")
                break

            time.sleep(args.sleep)

    else:
        # grid mode (nearby): intentionally fixed to avoid run-time tuning mistakes.
        min_lat, min_lng, max_lat, max_lng = DEFAULT_BBOX_TOKYO_23KU
        step_m = DEFAULT_GRID_STEP_M
        radius_m = DEFAULT_GRID_RADIUS_M
        included_types = list(DEFAULT_GRID_INCLUDED_TYPES)

        lat_mid = (min_lat + max_lat) / 2.0
        lat_step = meters_to_lat_deg(step_m)
        lng_step = meters_to_lng_deg(step_m, lat_mid)

        lats: List[float] = []
        cur = min_lat
        while cur <= max_lat + 1e-9:
            lats.append(cur)
            cur += lat_step

        lngs: List[float] = []
        cur = min_lng
        while cur <= max_lng + 1e-9:
            lngs.append(cur)
            cur += lng_step

        total_cells = len(lats) * len(lngs)

        print("GRID:")
        print(f"  bbox=({min_lat:.5f},{min_lng:.5f})-({max_lat:.5f},{max_lng:.5f})")
        print(f"  step_m={step_m:g} => lat_step≈{lat_step:.6f}deg lng_step≈{lng_step:.6f}deg")
        print(f"  cells={total_cells} (lat={len(lats)} x lng={len(lngs)})")
        print(f"  radius_m={radius_m:g} types={included_types}")

        # 注意: nearby は FieldMask都合で nextPageToken を使わない
        if args.max_pages != 1:
            print("NOTE: grid/nearby mode ignores --max-pages (pagination not used). Use tighter grid (step/radius) instead.")

        i = 0
        for lat in lats:
            for lng in lngs:
                i += 1
                res = nearby_search_new(
                    api_key=api_key,
                    center_lat=lat,
                    center_lng=lng,
                    radius_m=radius_m,
                    included_types=included_types,
                    language_code="ja",
                    region_code="JP",
                )
                ids = uniq_preserve_order(res.place_ids)
                added = add_global(ids)

                if i % 20 == 0 or added > 0:
                    print(f"[{i}/{total_cells}] hit={len(ids)} added={added} total_so_far={len(all_ids)}")

                if len(all_ids) >= args.hard_cap_ids:
                    print(f"STOP: reached hard cap ids={args.hard_cap_ids}")
                    break

                time.sleep(args.sleep)

            if len(all_ids) >= args.hard_cap_ids:
                break

    # outputs
    inbox_path = Path(args.inbox_out)
    write_lines(inbox_path, all_ids)

    out_new = args.out_new.strip()
    if not out_new:
        out_new = str(Path("out") / f"new_ids_{today_yyyy_mm_dd()}.txt")
    out_new_path = Path(out_new)

    new_only = [pid for pid in all_ids if pid not in master_ids]
    write_lines(out_new_path, new_only)

    print("OK:")
    print(f"  wrote inbox: {inbox_path} (count={len(all_ids)})")
    print(f"  wrote new  : {out_new_path} (count={len(new_only)})")
    print(f"  master_ids : {len(master_ids)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
