import requests
from bs4 import BeautifulSoup
import pandas as pd
import re
import time

def scrape_npb_data(year, month_num):
    m_str = str(month_num).zfill(2)
    # スケジュール詳細ページのURL
    list_url = f"https://npb.jp/games/{year}/schedule_{m_str}_detail.html"
    
    print(f"--- {year}年{month_num}月の解析を開始します ---")
    try:
        res = requests.get(list_url)
        res.encoding = 'utf-8'
        if res.status_code != 200:
            print(f"  {month_num}月のページが存在しません（スキップします）")
            return pd.DataFrame()
            
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # 詳細リンクの抽出（URLに指定した年が含まれるものを探す）
        links = [a['href'] for a in soup.find_all('a', href=re.compile(rf'/scores/{year}/'))]
        unique_links = sorted(list(set(links))) 
        
        if not unique_links:
            print(f"  {month_num}月の試合データは見つかりませんでした。")
            return pd.DataFrame()

        print(f"合計 {len(unique_links)} 試合が見つかりました。")
        game_data = []

        for link in unique_links:
            top_url = f"https://npb.jp{link}"
            print(f"解析中: {top_url}")
            time.sleep(1.5) # サーバー負荷を考慮し少し長めに設定
            
            try:
                res_top = requests.get(top_url)
                res_top.encoding = 'utf-8'
                soup_top = BeautifulSoup(res_top.text, 'html.parser')
                
                # スコアテーブルの取得
                score_table = soup_top.find('table', id='tablefix_ls')
                if not score_table: continue
                
                rows = score_table.find_all('tr')
                # rows[0]はヘッダー(回数)、rows[1]はビジター、rows[2]はホーム
                v_cells = [td.get_text(strip=True) for td in rows[1].find_all(['td', 'th'])]
                h_cells = [td.get_text(strip=True) for td in rows[2].find_all(['td', 'th'])]

                # 試合基本情報
                info_element = soup_top.find('p', class_='game_info')
                info_text = info_element.get_text() if info_element else ""
                attendance = re.search(r'入場者\s*([\d,]+)人', info_text)
                
                game_tit = soup_top.find('div', class_='game_tit')
                date = game_tit.find('time').get_text() if game_tit.find('time') else ""
                stadium = game_tit.find('span', class_='place').get_text() if game_tit.find('span', class_='place') else ""

                # イニング別得点（1-9回を抽出）
                v_inning_scores = v_cells[1:10]
                h_inning_scores = h_cells[1:10]
                inning_scores = ",".join([f"{h}({v})" for h, v in zip(h_inning_scores, v_inning_scores)])
                
                # 本塁打（セクションから抽出する方が確実）
                # ページ下部の本塁打セクションを探す
                sections = soup_top.find_all('section', class_='game_result_info')
                v_hr_count = 0
                h_hr_count = 0
                
                for section in sections:
                    h4 = section.find('h4')
                    if h4 and '本塁打' in h4.text:
                        tables = section.find_all('table')
                        for table in tables:
                            trs = table.find_all('tr')
                            for tr in trs:
                                team_name = tr.find('th').get_text() if tr.find('th') else ""
                                hr_text = tr.find('td').get_text() if tr.find('td') else ""
                                # 「なし」でなければ、読点やカンマで区切られた本数をカウント
                                if hr_text and "なし" not in hr_text:
                                    count = len(re.findall(r'\d+号', hr_text))
                                    if v_cells[0] in team_name: v_hr_count = count
                                    if h_cells[0] in team_name: h_hr_count = count

                game_data.append({
                    "日付": date,
                    "球場": stadium,
                    "ホーム球団": h_cells[0],
                    "ビジター球団": v_cells[0],
                    "ホーム得点": h_cells[-3],
                    "ビジター得点": v_cells[-3],
                    "ホーム安打": h_cells[-2],
                    "ビジター安打": v_cells[-2],
                    "ホーム失策": h_cells[-1],
                    "ビジター失策": v_cells[-1],
                    "ホームラン数（ホーム）": h_hr_count,
                    "ホームラン数（ビジター）": v_hr_count,
                    "イニング別得点（ホーム(ビジター)）": inning_scores,
                    "観客動員数": int(attendance.group(1).replace(',', '')) if attendance else 0,
                    "URL": top_url
                })
                
            except Exception as e:
                print(f"  エラー発生 ({link}): {e}")
                
    except Exception as e:
        print(f"  リストページ取得エラー: {e}")
            
    return pd.DataFrame(game_data)

# --- メイン処理 ---
TARGET_YEAR = 2022
all_dfs = []

# 2022年の全データを取得（プロ野球は通常3月〜10月）
for month in range(1, 13):
    df_month = scrape_npb_data(TARGET_YEAR, month)
    if not df_month.empty:
        all_dfs.append(df_month)
        print(f"  {month}月のデータ取得完了: {len(df_month)} 試合")

# すべての月のデータを統合
if all_dfs:
    df_year = pd.concat(all_dfs, ignore_index=True)
    output_file = f"npb_{TARGET_YEAR}_full.csv"
    df_year.to_csv(output_file, index=False, encoding="utf-8-sig")
    print("-" * 30)
    print(f"完了しました！ データを '{output_file}' に保存しました。")
    print(f"総試合数: {len(df_year)}")
else:
    print("データが取得できませんでした。")
