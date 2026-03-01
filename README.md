# Tokyo23ku Salon Lead Data

東京23区の美容室を対象に、Google Places API (New) を使って営業効率化データを生成するプロジェクトです。

本プロジェクトのゴールは「店舗リスト販売」ではなく、営業担当がすぐ使える優先度付きデータを毎月提供することです。

## 1. 事業コンセプト

- 商品: 東京23区 × 新規美容室 月次営業リスト
- 提供価値: 新規開拓にかかる探索工数を削減
- 提供形式: 月1回 CSV（将来的にPDFサマリー追加）
- 価格方針:
  - モニター: 5,000円
  - 本運用: 月額10,000円

## 2. 納品データ（初期版）

顧客向けCSVに含める想定カラム:

- `priority` (A/B/C)
- `score`
- `detected_first`
- `place_id` (内部向け運用で保持。顧客向けは非表示運用可)
- `name`
- `formatted_address`
- `ward`
- `rating`
- `user_rating_count`
- `phone`
- `website`
- `business_status`
- `google_maps_uri`

方針:
- `place_id` は内部管理のみ。顧客向けには非公開。

## 3. 新規判定ロジック

前提として Places API は開業日を直接返さないため、運用上の「初検出日」で新規を判定します。

- 東京23区を `grid + Nearby Search` で走査
- 取得した `place_id` を `master.json` と比較
- 初登場IDを新規候補として扱い、`detected_first`（= `first_seen`）を保存
- 月次抽出時に対象期間（例: 直近30日）でフィルタ

## 4. データパイプライン

1. `collect_place_ids_textsearch.py` で `place_id` 収集
2. `out/new_ids_*.txt` で差分確認
3. `update_master.py` で `data/master.json` を更新
4. `fetch_place_details.py` で新規候補の Details を取得
5. `build_monthly_csv.py` で月次CSVを出力

## 5. ディレクトリ構成

```text
.
├─ src/
│  ├─ collect_place_ids_textsearch.py   # place_id収集 (ward / grid)
│  ├─ update_master.py                  # master.json更新
│  ├─ fetch_place_details.py            # month指定でDetails取得
│  ├─ build_monthly_csv.py              # 月次CSV生成
│  └─ _later/
│     ├─ build_monthly_ids.py           # 月次IDリスト生成
│     └─ build_monthly_export.py        # 予約(未実装)
├─ data/
│  ├─ master.json
│  └─ master.json.bak
├─ inbox/
│  └─ place_ids*.txt
├─ out/
│  ├─ new_ids*.txt
│  └─ _later/monthly/*.json
├─ .env
└─ README.md
```

## 6. 実行コマンド

### 6.1 収集（grid例）

```powershell
python src/collect_place_ids_textsearch.py `
  --mode grid `
  --inbox-out inbox/place_ids_grid_2026-02-11.txt `
  --out-new out/new_ids_2026-02-11.txt `
  --master data/master.json
```

固定設定（コード内）:
- `bbox=35.53,139.57,35.83,139.92`
- `radius-m=1600`
- `step-m=1800`
- `types=hair_salon`

### 6.2 master更新

```powershell
python src/update_master.py `
  --dataset tokyo_salon `
  --timezone Asia/Tokyo `
  --area tokyo_23ku `
  --category salon `
  --today 2026-02-11 `
  --in inbox/place_ids_grid_2026-02-11.txt `
  --master data/master.json
```

### 6.3 月次ID生成（内部向け）

```powershell
python src/_later/build_monthly_ids.py --month 2026-02 --master data/master.json
```

### 6.4 Details取得（対象月の未取得のみ）

```powershell
python src/fetch_place_details.py `
  --month 2026-03 `
  --master data/master.json
```

補足:
- 対象: `first_seen` が指定月 (`YYYY-MM-*`) かつ `details_fetched_at` が空のレコード
- 強制再取得したい場合: `--force`
- API負荷を抑えて試す場合: `--limit 50`

### 6.5 月次CSV生成

```powershell
python src/build_monthly_csv.py `
  --month 2026-03 `
  --master data/master.json `
  --out out/monthly/current_leads.csv
```

補足:
- `website` と `google_maps_uri` は `HYPERLINK()` 形式で出力（Excelでクリックしやすくする）
- 生URLで出力したい場合: `--plain-url`

## 7. 運用頻度

推奨実行カレンダー:
- Place ID収集: `3日おき` + `27日` 追加実行
- Details取得: `14日` と `28日`
- CSV生成: Details直後に `out/monthly/current_leads.csv` を毎回出力
  - 14日版と28日版は同名ファイル運用（28日版で上書き）

運用メモ:
- 23区外の店舗が混ざる場合があるが、現運用では許容（副次的に取得できたリードとして扱う）

## 8. GitHub Actions運用方針

ローカル常駐ではなく GitHub 上での定期実行:

- `schedule` + `workflow_dispatch`
- APIキーは `GitHub Secrets` (`GOOGLE_MAPS_API_KEY`)
- 実装済みワークフロー:
  - `.github/workflows/collect_place_ids.yml`
  - `.github/workflows/details_and_export.yml`
- どちらも `contents: write` で自動コミット

## 9. APIとコストの考え方

- 主軸: Nearby Search Pro + 必要最小限の Place Details
- 想定利用量: 月700〜800件程度（見積り）
- コスト最適化:
  - FieldMaskを最小化
  - Detailsは新規候補のみ

## 10. 注意事項（規約・データ品質）

- Google Maps Platform利用規約に従うこと
- 顧客向け提供は「営業判断に使える加工情報」を中心にすること
- 品質管理で最低限実施すること:
  - 重複除去
  - 欠損ルール統一
  - A/B/C 判定閾値の固定

## 11. 現在の進捗（2026-03-01）

- コンセプト: 確定
- 価格感: 確定
- 技術構造: 実装済み
- 新規判定: 初検出ベースで確定
- ID収集・Details・CSV生成・Actions: 一連動作確認済み

## 12. 次の実装タスク

1. GitHub Secrets/Permissions の本番反映
2. Actions本番運用開始（dry-run後に定期実行）
3. A/B/C閾値の月次見直し（実績ベース）
4. 営業文面テンプレート整備
