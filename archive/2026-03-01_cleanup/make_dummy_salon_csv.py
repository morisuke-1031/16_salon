# make_dummy_salon_csv.py
# Preflight: passed
from __future__ import annotations

import argparse
import csv
import random
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import List, Tuple


WARDS_23 = [
    "千代田区","中央区","港区","新宿区","文京区","台東区","墨田区","江東区","品川区","目黒区","大田区","世田谷区",
    "渋谷区","中野区","杉並区","豊島区","北区","荒川区","板橋区","練馬区","足立区","葛飾区","江戸川区"
]

AREAS_BY_WARD = {
    "千代田区": ["神田", "秋葉原", "丸の内", "麹町"],
    "中央区": ["銀座", "日本橋", "月島", "築地"],
    "港区": ["六本木", "麻布", "赤坂", "芝"],
    "新宿区": ["新宿", "高田馬場", "神楽坂", "四谷"],
    "文京区": ["本郷", "小石川", "千駄木"],
    "台東区": ["上野", "浅草", "蔵前"],
    "墨田区": ["錦糸町", "押上", "両国"],
    "江東区": ["豊洲", "門前仲町", "東陽町"],
    "品川区": ["大井町", "五反田", "天王洲"],
    "目黒区": ["中目黒", "自由が丘", "学芸大学"],
    "大田区": ["蒲田", "大森", "田園調布"],
    "世田谷区": ["下北沢", "三軒茶屋", "二子玉川"],
    "渋谷区": ["渋谷", "恵比寿", "代官山", "原宿"],
    "中野区": ["中野", "東中野", "野方"],
    "杉並区": ["高円寺", "阿佐ヶ谷", "荻窪"],
    "豊島区": ["池袋", "巣鴨", "目白"],
    "北区": ["赤羽", "王子", "田端"],
    "荒川区": ["日暮里", "西日暮里", "町屋"],
    "板橋区": ["板橋", "成増", "大山"],
    "練馬区": ["練馬", "石神井", "大泉学園"],
    "足立区": ["北千住", "綾瀬", "西新井"],
    "葛飾区": ["亀有", "金町", "新小岩"],
    "江戸川区": ["小岩", "葛西", "船堀"],
}

NAME_PREFIX = ["Lumi", "moi", "Rin", "Aile", "Nalu", "Sora", "Nico", "Kuu", "Haru", "Noa", "Rosa", "Mimi"]
NAME_CORE = ["Salon", "HAIR", "Beauty", "Atelier", "Room", "Studio", "Lounge", "Works"]
NAME_SUFFIX = ["", "Tokyo", "Ginza", "Shibuya", "Kagurazaka", "Kichijoji", "Ebisu", "Omotesando"]

STREET_SUFFIX = ["", "一丁目", "二丁目", "三丁目", "四丁目"]
BLOCK_SUFFIX = ["1-2-3", "2-5-8", "3-7-1", "1-1-9", "4-2-6", "5-1-2"]
BUILDINGS = ["〇〇ビル", "△△レジデンス", "□□タワー", "サンライトビル", "グリーンハイツ", "コーポさくら", "ルミエール"]

def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x

def iso(d: date) -> str:
    return d.isoformat()

def make_fake_place_id(rng: random.Random) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return "ChI" + "".join(rng.choice(alphabet) for _ in range(24))

def make_google_maps_uri(place_id: str) -> str:
    # Place Details (New) だと googleMapsUri/googleMapsLinks が取れるが、
    # ダミーは「検索URL」で代用しておく（実運用時はgoogleMapsUriに差し替え）
    return f"https://www.google.com/maps/search/?api=1&query_place_id={place_id}"

def make_name(rng: random.Random) -> str:
    return f"{rng.choice(NAME_PREFIX)} {rng.choice(NAME_CORE)} {rng.choice(NAME_SUFFIX)}".strip()

def make_address(ward: str, rng: random.Random) -> str:
    area = rng.choice(AREAS_BY_WARD.get(ward, ["〇〇"]))
    street = area + rng.choice(STREET_SUFFIX)
    block = rng.choice(BLOCK_SUFFIX)
    bld = rng.choice(BUILDINGS)
    # 日本の住所っぽく
    return f"東京都{ward}{street}{block} {bld}"

def ward_from_address(addr: str) -> str:
    # "東京都港区..." から区を抜く
    m = re.search(r"東京都([^都道府県]+?区)", addr)
    return m.group(1) if m else ""

def score_row(days_since_detected: int, rating: float | None, review_count: int | None) -> float:
    # 売り物として「営業の回る順番」を作るための単純スコア
    # 新しさ: 最大50点（0日=50点、30日=0点）
    freshness = clamp(50.0 * (1.0 - (days_since_detected / 30.0)), 0.0, 50.0)

    # レビュー数: 最大30点（0=0、50以上=30）
    rc = 0 if review_count is None else max(0, review_count)
    review_score = clamp((rc / 50.0) * 30.0, 0.0, 30.0)

    # 星: 最大20点（3.5=0、4.8=20）
    if rating is None:
        rating_score = 0.0
    else:
        rating_score = clamp(((rating - 3.5) / (4.8 - 3.5)) * 20.0, 0.0, 20.0)

    return round(freshness + review_score + rating_score, 2)

def priority_from(score: float, days_since_detected: int, rating: float | None, review_count: int | None) -> str:
    # A: 今すぐ回る（新しい+ある程度評価/レビュー）
    # B: 新しいが材料薄い（先物）
    # C: 弱い/保留
    rc = 0 if review_count is None else review_count
    rt = 0.0 if rating is None else rating

    if days_since_detected <= 14 and rc >= 8 and rt >= 4.0:
        return "A"
    if days_since_detected <= 30 and (rc >= 3 or rt >= 3.8):
        return "B"
    return "C"

@dataclass
class Row:
    priority: str
    score: float
    detected_first: str
    place_id: str
    name: str
    formatted_address: str
    ward: str
    rating: float | None
    user_rating_count: int | None
    business_status: str
    google_maps_uri: str

def generate_dummy(
    n: int,
    month: str,
    seed: int,
    include_enterprise_fields: bool,
) -> List[Row]:
    rng = random.Random(seed)

    # month = "2026-02" を想定 → その月の1日〜末日の範囲で「初検出日」を作る
    month_dt = datetime.strptime(month + "-01", "%Y-%m-%d").date()
    # 次月1日
    if month_dt.month == 12:
        next_month = date(month_dt.year + 1, 1, 1)
    else:
        next_month = date(month_dt.year, month_dt.month + 1, 1)
    last_day = next_month - timedelta(days=1)

    rows: List[Row] = []
    for _ in range(n):
        ward = rng.choice(WARDS_23)
        addr = make_address(ward, rng)
        pid = make_fake_place_id(rng)
        guri = make_google_maps_uri(pid)
        nm = make_name(rng)

        # 初検出日は月内ランダム（新規が多いほど直近寄りにする）
        # 直近寄り: 三角分布っぽく
        day_offset = int(rng.triangular(0, (last_day - month_dt).days, (last_day - month_dt).days))
        detected = month_dt + timedelta(days=day_offset)
        days_since = (last_day - detected).days  # 「月末時点で何日前に検出されたか」みたいな見立て

        business_status = rng.choices(
            ["OPERATIONAL", "CLOSED_TEMPORARILY", "CLOSED_PERMANENTLY"],
            weights=[0.94, 0.04, 0.02],
            k=1
        )[0]

        if include_enterprise_fields:
            # それっぽい分布：新店はレビュー少なめだが、たまに伸びる店がある
            # review_countはポアソン風、上限はざっくり60
            base = max(0, int(rng.gauss(6, 6)))
            # 直近ほど少なめに補正
            adj = int(clamp(base - (14 - min(14, days_since)) * 0.4, 0, 60))
            # たまにバズる
            if rng.random() < 0.08:
                adj = min(60, adj + rng.randint(15, 40))
            review_count = adj

            # ratingはレビュー数が少ないと欠損する想定も混ぜる
            if review_count == 0 and rng.random() < 0.6:
                rating = None
            else:
                rating = round(clamp(rng.gauss(4.25, 0.25), 3.2, 4.9), 1)
        else:
            rating = None
            review_count = None

        sc = score_row(days_since, rating, review_count)
        pr = priority_from(sc, days_since, rating, review_count)

        # 閉業はC固定にしても良いが、ここではスコアには反映せず、出力側で除外できるようにする
        if business_status != "OPERATIONAL":
            pr = "C"

        rows.append(Row(
            priority=pr,
            score=sc,
            detected_first=iso(detected),
            place_id=pid,
            name=nm,
            formatted_address=addr,
            ward=ward_from_address(addr) or ward,
            rating=rating,
            user_rating_count=review_count,
            business_status=business_status,
            google_maps_uri=guri
        ))

    # まずは営業の「回る順番」：A→B→C、スコア降順、検出日新しい順
    def sort_key(r: Row) -> Tuple[int, float, str]:
        pri_rank = {"A": 0, "B": 1, "C": 2}.get(r.priority, 9)
        return (pri_rank, -r.score, r.detected_first)

    rows.sort(key=sort_key)
    return rows

def write_csv(path: str, rows: List[Row]) -> None:
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
        "business_status",
        "google_maps_uri",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({
                "priority": r.priority,
                "score": r.score,
                "detected_first": r.detected_first,
                "place_id": r.place_id,
                "name": r.name,
                "formatted_address": r.formatted_address,
                "ward": r.ward,
                "rating": "" if r.rating is None else r.rating,
                "user_rating_count": "" if r.user_rating_count is None else r.user_rating_count,
                "business_status": r.business_status,
                "google_maps_uri": r.google_maps_uri,
            })

def main() -> None:
    ap = argparse.ArgumentParser(description="Generate dummy Tokyo 23-ward salon CSV for product design.")
    ap.add_argument("--out", default="dummy_tokyo_salon.csv", help="Output CSV path")
    ap.add_argument("--n", type=int, default=120, help="Number of rows")
    ap.add_argument("--month", default=date.today().strftime("%Y-%m"), help="Target month like 2026-02")
    ap.add_argument("--seed", type=int, default=42, help="Random seed")
    ap.add_argument(
        "--include-enterprise-fields",
        action="store_true",
        help="Include rating & user_rating_count (simulate Place Details Enterprise fields)."
    )
    args = ap.parse_args()

    if args.n <= 0 or args.n > 5000:
        raise SystemExit("Error: --n must be between 1 and 5000")

    rows = generate_dummy(
        n=args.n,
        month=args.month,
        seed=args.seed,
        include_enterprise_fields=args.include_enterprise_fields,
    )
    write_csv(args.out, rows)
    print(f"OK: wrote {len(rows)} rows -> {args.out}")

if __name__ == "__main__":
    main()
