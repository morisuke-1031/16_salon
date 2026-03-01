from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


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

    # Windows PowerShell の Set-Content -Encoding UTF8 などで
    # UTF-8 BOM付きで保存されることがあるため utf-8-sig で読む（BOMがあれば除去、無ければ通常utf-8）
    raw = path.read_text(encoding="utf-8-sig").strip()
    if raw == "":
        raise ValueError(f"master.json is empty: {path}")

    obj = json.loads(raw)
    if not isinstance(obj, dict) or "places" not in obj or not isinstance(obj["places"], dict):
        raise ValueError("Invalid master.json format")
    return obj


def main() -> int:
    ap = argparse.ArgumentParser(description="Build monthly place_id list from master.json by first_seen in month.")
    ap.add_argument("--month", required=True, help="YYYY-MM (JST)")
    ap.add_argument("--master", default="data/master.json", help="Path to master.json")
    ap.add_argument("--out", default="", help="Output path; default: out/monthly/YYYY-MM_ids.json")
    args = ap.parse_args()

    month = normalize_yyyy_mm(args.month)
    master = load_master(Path(args.master))
    places: Dict[str, Any] = master["places"]

    prefix = month + "-"
    ids: List[str] = [str(pid) for pid, rec in places.items() if str(rec.get("first_seen", "")).startswith(prefix)]
    ids.sort()

    out_path = Path(args.out) if args.out else Path("out") / "monthly" / f"{month}_ids.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "meta": {
            "month": month,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "count": len(ids),
        },
        "place_ids": ids,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK: {out_path} (count={len(ids)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
