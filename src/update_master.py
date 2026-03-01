from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple


# -------- time / date --------
def jst_now_iso() -> str:
    # ローカルPC想定（JST）。GitHub ActionsでもTZ=Asia/Tokyoを設定すればOK。
    now = datetime.now().astimezone()
    return now.isoformat(timespec="seconds")


def normalize_date_yyyy_mm_dd(s: str) -> str:
    parts = s.strip().split("-")
    if len(parts) != 3:
        raise ValueError(f"Invalid date (expected YYYY-MM-DD): {s!r}")
    y, m, d = parts
    if not (len(y) == 4 and len(m) == 2 and len(d) == 2 and y.isdigit() and m.isdigit() and d.isdigit()):
        raise ValueError(f"Invalid date (expected YYYY-MM-DD): {s!r}")
    # validate
    datetime(int(y), int(m), int(d))
    return f"{y}-{m}-{d}"


# -------- place_id normalize --------
BOM_CHAR = "\ufeff"


def normalize_place_id(s: str) -> str:
    # 行頭BOM混入（﻿ChIJ...）を確実に除去
    s = s.strip()
    if not s:
        return ""
    if s[0] == BOM_CHAR:
        s = s.lstrip(BOM_CHAR).strip()
    # place_id内に空白が混ざるのは異常（コピペ事故）
    if any(ch.isspace() for ch in s):
        raise ValueError(f"Invalid place_id (contains whitespace): {s!r}")
    return s


def read_place_ids(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(f"place_ids file not found: {path}")

    # utf-8-sig で読むと、先頭BOMは自動で剥がれる（ただし行頭BOMは残るケースがあるので normalize_place_id でも剥ぐ）
    lines = path.read_text(encoding="utf-8-sig").splitlines()

    ids: List[str] = []
    for line in lines:
        pid = normalize_place_id(line)
        if not pid:
            continue
        ids.append(pid)

    # uniq preserve order
    seen = set()
    uniq: List[str] = []
    for pid in ids:
        if pid in seen:
            continue
        seen.add(pid)
        uniq.append(pid)
    return uniq


# -------- master io --------
def _new_master(dataset: str, tz: str) -> Dict[str, Any]:
    now = jst_now_iso()
    return {
        "meta": {
            "schema": 1,
            "dataset": dataset,
            "timezone": tz,
            "created_at": now,
            "updated_at": now,
        },
        "places": {},
        "runs": [],
    }


def load_master(path: Path, dataset: str, tz: str) -> Dict[str, Any]:
    if not path.exists():
        return _new_master(dataset, tz)

    # BOMを含んだJSONでも確実に読める
    raw = path.read_text(encoding="utf-8-sig").strip()

    if raw == "":
        # 0バイト/空白だけの壊れファイルは新規として復旧
        return _new_master(dataset, tz)

    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError(f"Invalid master.json (not an object): {path}")

    if "meta" not in obj or not isinstance(obj["meta"], dict):
        obj["meta"] = {}
    if "places" not in obj:
        obj["places"] = {}
    if "runs" not in obj:
        obj["runs"] = []

    if not isinstance(obj["places"], dict):
        raise ValueError("master.places must be an object keyed by place_id")
    if not isinstance(obj["runs"], list):
        raise ValueError("master.runs must be a list")

    # dataset/tz は“上書きしない”方針（運用で変わると混乱するため）
    # ただし空なら埋める
    obj["meta"].setdefault("schema", 1)
    obj["meta"].setdefault("dataset", dataset)
    obj["meta"].setdefault("timezone", tz)
    obj["meta"].setdefault("created_at", jst_now_iso())
    obj["meta"]["updated_at"] = jst_now_iso()

    return obj


def save_master(path: Path, master: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    master["meta"]["updated_at"] = jst_now_iso()
    # 書き込みはBOM無しUTF-8に固定（Windowsでも安定）
    path.write_text(json.dumps(master, ensure_ascii=False, indent=2), encoding="utf-8")


# -------- update logic --------
def ensure_place_shape(rec: Dict[str, Any]) -> None:
    # 将来の拡張枠：いまはnullで良い
    if "details_min" not in rec:
        rec["details_min"] = None
    if "details_fetched_at" not in rec:
        rec["details_fetched_at"] = None


def upsert_places(
    master: Dict[str, Any],
    place_ids: List[str],
    today: str,
    area: str,
    category: str,
    run_id: str,
) -> Tuple[int, int]:
    places: Dict[str, Any] = master["places"]
    new_count = 0
    seen_count = 0

    for pid_raw in place_ids:
        pid = normalize_place_id(pid_raw)
        if not pid:
            continue

        rec = places.get(pid)
        if rec is None:
            rec = {
                "place_id": pid,
                "first_seen": today,
                "last_seen": today,
                "area": area,
                "category": category,
                "first_seen_run": run_id,
                "last_seen_run": run_id,
                "details_min": None,
                "details_fetched_at": None,
            }
            places[pid] = rec
            new_count += 1
        else:
            # 既存：観測更新
            rec["last_seen"] = today
            rec["last_seen_run"] = run_id
            # area/categoryは「最初に見つけた文脈」を残したいので基本は上書きしない
            # ただし欠けていれば埋める
            rec.setdefault("area", area)
            rec.setdefault("category", category)
            ensure_place_shape(rec)
            seen_count += 1

    return new_count, seen_count


def append_run_log(
    master: Dict[str, Any],
    run_id: str,
    today: str,
    area: str,
    category: str,
    found: int,
    new: int,
) -> None:
    now = jst_now_iso()
    master["runs"].append(
        {
            "run_id": run_id,
            "date": today,
            "started_at": now,
            "ended_at": now,
            "area": area,
            "category": category,
            "found": found,
            "new": new,
        }
    )
    # 肥大化防止
    if len(master["runs"]) > 200:
        master["runs"] = master["runs"][-200:]


def main() -> int:
    ap = argparse.ArgumentParser(description="Update master.json with place_ids (IDs-only, observation-based).")
    ap.add_argument("--dataset", default="tokyo_salon", help="Dataset name in meta.")
    ap.add_argument("--timezone", default="Asia/Tokyo", help="Timezone string stored in meta.")
    ap.add_argument("--area", required=True, help="e.g. tokyo_23ku")
    ap.add_argument("--category", required=True, help="e.g. salon")
    ap.add_argument("--today", required=True, help="YYYY-MM-DD (JST)")
    ap.add_argument("--in", dest="in_path", required=True, help="Path to place_ids.txt (1 place_id per line)")
    ap.add_argument("--master", default="data/master.json", help="Path to master.json")
    ap.add_argument("--run-id", default="", help="Optional run_id; default: YYYY-MM-DD_area_category")
    args = ap.parse_args()

    today = normalize_date_yyyy_mm_dd(args.today)
    in_path = Path(args.in_path)
    master_path = Path(args.master)

    run_id = args.run_id.strip() or f"{today}_{args.area}_{args.category}"

    place_ids = read_place_ids(in_path)
    master = load_master(master_path, dataset=args.dataset, tz=args.timezone)

    new_count, seen_count = upsert_places(
        master=master,
        place_ids=place_ids,
        today=today,
        area=args.area,
        category=args.category,
        run_id=run_id,
    )

    append_run_log(
        master=master,
        run_id=run_id,
        today=today,
        area=args.area,
        category=args.category,
        found=len(place_ids),
        new=new_count,
    )

    save_master(master_path, master)

    print(f"OK: master updated: {master_path}")
    print(f"  run_id={run_id}")
    print(f"  found={len(place_ids)} new={new_count} existing={seen_count}")
    print(f"  total_places={len(master['places'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
