"""
NPB公式サイト 試合データ スクレイピングスクリプト v2
https://npb.jp/scores/{年}/{月日}/{カード}/

【元のコードからの改善点】
  1. HR取得のチームマッチングを略称対応に修正
  2. イニング取得を延長戦対応（9回固定 → 可変）に修正
  3. 逆転・サヨナラ・延長・リード変化回数の判定を追加
  4. 複数年に対応
  5. 曜日カラムを追加（交絡変数として分析で使用）

必要ライブラリ:
    pip install requests beautifulsoup4 pandas

実行方法:
    python npb_scraper_v2.py
"""

import re
import time
import requests
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime


# ============================================================
# 設定
# ============================================================

TARGET_YEARS = [2022]   # 取得対象年（複数指定可）
SLEEP_SEC    = 1.5                  # サーバー負荷対策（変更しないこと）

# チーム略称 → フルネームの一部（HR取得のマッチング用）
TEAM_ABBR = {
    "巨人":       "読売",
    "阪神":       "阪神",
    "DeNA":       "DeNA",
    "横浜":       "DeNA",
    "中日":       "中日",
    "広島":       "広島",
    "ヤクルト":   "ヤクルト",
    "ソフトバンク": "ソフトバンク",
    "日本ハム":   "日本ハム",
    "オリックス": "オリックス",
    "楽天":       "楽天",
    "西武":       "西武",
    "ロッテ":     "ロッテ",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# ============================================================
# メイン取得関数
# ============================================================

def scrape_npb_data(year, month_num):
    """1ヶ月分の試合データを取得してDataFrameで返す"""
    m_str = str(month_num).zfill(2)
    list_url = f"https://npb.jp/games/{year}/schedule_{m_str}_detail.html"

    print(f"\n--- {year}年{month_num}月 の取得開始 ---")
    try:
        res = requests.get(list_url, headers=HEADERS, timeout=10)
        res.encoding = "utf-8"
        if res.status_code != 200:
            print(f"  ページなし（スキップ）: {list_url}")
            return pd.DataFrame()

        soup = BeautifulSoup(res.text, "html.parser")

        # 試合URLを収集
        links = [
            a["href"] for a in soup.find_all("a", href=re.compile(rf"/scores/{year}/"))
        ]
        unique_links = sorted(set(links))

        if not unique_links:
            print(f"  試合データなし")
            return pd.DataFrame()

        print(f"  {len(unique_links)} 試合を発見")
        game_data = []

        for link in unique_links:
            top_url = f"https://npb.jp{link}"
            print(f"  取得中: {top_url}")
            time.sleep(SLEEP_SEC)

            try:
                game = scrape_single_game(top_url, year)
                if game:
                    game_data.append(game)
            except Exception as e:
                print(f"    エラー ({link}): {e}")

        return pd.DataFrame(game_data)

    except Exception as e:
        print(f"  リストページエラー: {e}")
        return pd.DataFrame()


def scrape_single_game(url, year):
    """1試合分のデータを取得して辞書で返す"""
    res = requests.get(url, headers=HEADERS, timeout=10)
    res.encoding = "utf-8"
    soup = BeautifulSoup(res.text, "html.parser")

    # スコアテーブル
    score_table = soup.find("table", id="tablefix_ls")
    if not score_table:
        return None

    rows = score_table.find_all("tr")
    if len(rows) < 3:
        return None

    # イニング別スコア・合計・安打・失策を取得
    v_inn, v_tot, v_hits, v_errors = parse_score_row(rows[1])
    h_inn, h_tot, h_hits, h_errors = parse_score_row(rows[2])

    # チーム名取得
    v_team = get_team_name(rows[1])
    h_team = get_team_name(rows[2])

    if v_tot is None or h_tot is None:
        return None  # 試合未完了はスキップ

    # 試合基本情報
    info_el = soup.find("p", class_="game_info")
    info_text = info_el.get_text() if info_el else ""

    # 試合終了チェック
    if "試合終了" not in info_text:
        return None

    attendance_m = re.search(r"入場者\s*([\d,]+)人", info_text)
    attendance = int(attendance_m.group(1).replace(",", "")) if attendance_m else None

    duration_m = re.search(r"試合時間\s*([^\s◇]+)", info_text)
    duration_str = duration_m.group(1) if duration_m else ""

    game_tit = soup.find("div", class_="game_tit")
    date_str  = game_tit.find("time").get_text() if game_tit and game_tit.find("time") else ""
    stadium   = game_tit.find("span", class_="place").get_text() if game_tit and game_tit.find("span", class_="place") else ""

    # 曜日（交絡変数として重要）
    weekday, is_weekend, is_holiday = get_weekday_info(date_str, year)

    # 本塁打（チーム別）
    result_section = soup.find("section", class_="game_result_info")
    v_hr, h_hr, v_hr_text, h_hr_text = extract_homeruns(result_section, v_team, h_team)

    # 勝敗投手
    win_p  = extract_pitcher(result_section, "勝投手")
    lose_p = extract_pitcher(result_section, "敗投手")
    save_p = extract_pitcher(result_section, "セーブ")

    # ===== 派生指標（翌日観客分析の核心） =====
    is_home_win    = 1 if h_tot > v_tot else 0
    score_diff     = abs(h_tot - v_tot)
    is_comeback    = check_comeback(v_inn, h_inn, h_tot, v_tot)
    is_sayonara    = check_sayonara(h_inn, h_tot, v_tot)
    is_extra       = 1 if len(v_inn) > 9 else 0
    lead_changes   = count_lead_changes(v_inn, h_inn)   # リードの入れ替わり回数
    last_score_inn = last_scoring_inning(v_inn, h_inn)  # 最後に得点が入ったイニング

    # イニング別得点（文字列で保存）
    inning_scores = ",".join([
        f"{h}({v})" for h, v in zip(h_inn, v_inn)
    ])

    return {
        # 基本情報
        "年度":             year,
        "日付":             date_str,
        "曜日":             weekday,
        "週末/祝日":         is_weekend,
        "球場":             stadium,
        "試合時間":          duration_str,
        # チーム・スコア
        "ホーム球団":        h_team,
        "ビジター球団":      v_team,
        "ホーム得点":        h_tot,
        "ビジター得点":      v_tot,
        "ホーム安打":        h_hits,
        "ビジター安打":      v_hits,
        "ホーム失策":        h_errors,
        "ビジター失策":      v_errors,
        "ホームラン_ホーム":  h_hr,
        "ホームラン_ビジター": v_hr,
        "ホームラン合計":    h_hr + v_hr,
        "HR詳細_ホーム":     h_hr_text,
        "HR詳細_ビジター":   v_hr_text,
        # 勝敗
        "ホーム勝利":        is_home_win,
        "得点差":            score_diff,
        # ===== 分析の核心：試合の「劇的さ」指標 =====
        "逆転あり":          is_comeback,    # 1=逆転あり, 0=なし
        "サヨナラ":          is_sayonara,    # 1=サヨナラ, 0=なし
        "延長戦":            is_extra,       # 1=延長, 0=なし
        "リード変化回数":     lead_changes,   # 0=一方的, 多いほど接戦
        "最終得点イニング":   last_score_inn, # 9回に点が入ったか
        # イニング
        "イニング別得点":     inning_scores,
        # 投手
        "勝利投手":          win_p,
        "敗戦投手":          lose_p,
        "セーブ投手":        save_p,
        # URL
        "URL":               f"https://npb.jp{url}" if not url.startswith("http") else url,
    }


# ============================================================
# パース関数
# ============================================================

def parse_score_row(row):
    """
    スコア行からイニング別得点・合計・安打・失策を取得する
    延長戦対応：total-1クラスの手前までをイニングとして扱う
    """
    innings = []
    total = hits = errors = None

    for td in row.find_all("td"):
        cls = td.get("class", [])
        txt = td.get_text(strip=True)

        if "total-1" in cls:
            try: total = int(txt)
            except: pass
        elif "total-2" in cls:
            try:
                v = int(txt)
                if hits is None: hits = v
                else: errors = v
            except: pass
        else:
            # イニング得点
            if txt == "x":
                innings.append("x")
            elif txt.isdigit():
                innings.append(int(txt))
            else:
                innings.append(txt)

    return innings, total, hits, errors


def get_team_name(row):
    """スコア行からチーム名を取得する（hide_sp = PC表示名を優先）"""
    th = row.find("th")
    if not th:
        return ""
    hide_sp = th.find("span", class_="hide_sp")
    if hide_sp:
        return hide_sp.get_text(strip=True)
    return th.get_text(strip=True)


def extract_homeruns(section, v_team, h_team):
    """
    本塁打テーブルからvisitor/home別に取得する
    略称マッチング対応（例: 「ロッテ」→「千葉ロッテマリーンズ」）
    """
    v_hr = h_hr = 0
    v_hr_text = h_hr_text = ""

    if not section:
        return v_hr, h_hr, v_hr_text, h_hr_text

    hr_table = None
    for h4 in section.find_all("h4"):
        if "本塁打" in h4.get_text():
            hr_table = h4.find_next_sibling("table")
            break

    if not hr_table:
        return v_hr, h_hr, v_hr_text, h_hr_text

    for row in hr_table.find_all("tr"):
        th = row.find("th")
        td = row.find("td")
        if not th or not td:
            continue
        th_text = th.get_text(strip=True).strip("【】")
        td_text = td.get_text(strip=True)

        # 「なし」の場合はスキップ
        if "なし" in td_text:
            continue

        hr_count = len(re.findall(r"\d+号", td_text))

        # 略称でマッチング
        if team_matches(th_text, v_team):
            v_hr = hr_count
            v_hr_text = td_text
        elif team_matches(th_text, h_team):
            h_hr = hr_count
            h_hr_text = td_text

    return v_hr, h_hr, v_hr_text, h_hr_text


def team_matches(abbr, full_name):
    """チーム略称がフルネームに含まれるか判定する"""
    if abbr in full_name:
        return True
    mapped = TEAM_ABBR.get(abbr, abbr)
    return mapped in full_name


def extract_pitcher(section, label):
    """勝投手・敗投手・セーブを抽出する"""
    if not section:
        return ""
    for row in section.find_all("tr"):
        th = row.find("th")
        td = row.find("td")
        if th and td and label in th.get_text():
            return td.get_text(strip=True)
    return ""


def get_weekday_info(date_str, year):
    """
    日付文字列から曜日・週末フラグを取得する
    例: "2024年5月10日（金）" → ("金", 0, 0)
    """
    weekday = ""
    is_weekend = 0

    m = re.search(r"（(.+?)）", date_str)
    if m:
        weekday = m.group(1)
        if weekday in ["土", "日"]:
            is_weekend = 1

    # 祝日判定（簡易版：月・日を抽出して主要祝日をカバー）
    is_holiday = 0
    date_m = re.search(r"(\d+)年(\d+)月(\d+)日", date_str)
    if date_m:
        month = int(date_m.group(2))
        day   = int(date_m.group(3))
        # 主要祝日（振替含まず簡易版）
        holidays = {
            (1,1),(1,8),(2,11),(2,23),(3,20),(4,29),
            (5,3),(5,4),(5,5),(7,15),(8,11),(9,16),
            (9,23),(10,14),(11,3),(11,23)
        }
        if (month, day) in holidays:
            is_holiday = 1
            is_weekend = 1  # 祝日も週末と同様に扱う

    return weekday, is_weekend, is_holiday


# ============================================================
# 派生指標の計算
# ============================================================

def check_comeback(v_inn, h_inn, h_tot, v_tot):
    """
    逆転勝ちかどうかを判定する
    「一度でも負けていた側が最終的に勝つ」= 逆転
    """
    v_cum = h_cum = 0
    home_was_trailing  = False
    away_was_trailing  = False

    for i, (v, h) in enumerate(zip(v_inn, h_inn)):
        v_score = v if isinstance(v, int) else 0
        h_score = 0 if h == "x" else (h if isinstance(h, int) else 0)
        v_cum += v_score
        h_cum += h_score

        if i > 0:  # 初回から負けてる場合は逆転ではない
            if v_cum > h_cum: home_was_trailing = True
            if h_cum > v_cum: away_was_trailing = True

    is_home_win = h_tot > v_tot
    if is_home_win and home_was_trailing:
        return 1
    if not is_home_win and away_was_trailing:
        return 1
    return 0


def check_sayonara(h_inn, h_tot, v_tot):
    """
    サヨナラ勝ちかどうかを判定する
    「最終イニングが'x'かつホーム勝利」= サヨナラ
    """
    if h_tot <= v_tot:
        return 0
    if h_inn and h_inn[-1] == "x":
        return 1
    return 0


def count_lead_changes(v_inn, h_inn):
    """
    リードが入れ替わった回数をカウントする
    0 = 一方的な試合, 多いほど接戦・見応えあり
    """
    v_cum = h_cum = 0
    prev_lead = 0   # 0=同点, 1=visitor先行, -1=home先行
    changes = 0

    for v, h in zip(v_inn, h_inn):
        v_score = v if isinstance(v, int) else 0
        h_score = 0 if h == "x" else (h if isinstance(h, int) else 0)
        v_cum += v_score
        h_cum += h_score

        cur_lead = 1 if v_cum > h_cum else (-1 if h_cum > v_cum else 0)
        if prev_lead != 0 and cur_lead != 0 and cur_lead != prev_lead:
            changes += 1
        if cur_lead != 0:
            prev_lead = cur_lead

    return changes


def last_scoring_inning(v_inn, h_inn):
    """
    最後に得点が入ったイニングを返す（終盤の盛り上がり指標）
    例: 9 → 9回に点が入った（劇的）, 3 → 3回以降得点なし
    """
    last = 0
    for i, (v, h) in enumerate(zip(v_inn, h_inn), 1):
        v_score = v if isinstance(v, int) else 0
        h_score = 0 if h == "x" else (h if isinstance(h, int) else 0)
        if v_score > 0 or h_score > 0:
            last = i
    return last


# ============================================================
# メイン処理
# ============================================================

def main():
    all_dfs = []

    for year in TARGET_YEARS:
        print(f"\n{'='*50}")
        print(f"  {year}年 スクレイピング開始")
        print(f"{'='*50}")

        year_dfs = []
        for month in range(1, 13):
            df_month = scrape_npb_data(year, month)
            if not df_month.empty:
                year_dfs.append(df_month)
                print(f"  → {month}月: {len(df_month)} 試合取得")

        if year_dfs:
            df_year = pd.concat(year_dfs, ignore_index=True)
            all_dfs.append(df_year)
            print(f"\n  {year}年 合計: {len(df_year)} 試合")

    if not all_dfs:
        print("データが取得できませんでした")
        return

    df_all = pd.concat(all_dfs, ignore_index=True)

    # 出力
    years_str  = f"{min(TARGET_YEARS)}_{max(TARGET_YEARS)}"
    output_file = f"npb_{years_str}_full.csv"
    df_all.to_csv(output_file, index=False, encoding="utf-8-sig")

    print(f"\n{'='*50}")
    print(f"✅ 保存完了: {output_file}")
    print(f"   総試合数: {len(df_all):,} 試合")
    print(f"\n--- 派生指標の集計 ---")
    print(f"  逆転試合:    {df_all['逆転あり'].sum():,} 試合 ({df_all['逆転あり'].mean()*100:.1f}%)")
    print(f"  サヨナラ:    {df_all['サヨナラ'].sum():,} 試合 ({df_all['サヨナラ'].mean()*100:.1f}%)")
    print(f"  延長戦:      {df_all['延長戦'].sum():,} 試合 ({df_all['延長戦'].mean()*100:.1f}%)")
    print(f"  平均得点差:  {df_all['得点差'].mean():.2f} 点")
    print(f"\n--- サンプルデータ（先頭3行）---")
    cols = ["日付", "曜日", "週末/祝日", "ホーム球団", "ホーム得点",
            "ビジター得点", "ビジター球団", "観客動員数" if "観客動員数" in df_all.columns else "逆転あり",
            "逆転あり", "サヨナラ", "リード変化回数"]
    print(df_all[[c for c in cols if c in df_all.columns]].head(3).to_string(index=False))


if __name__ == "__main__":
    main()
