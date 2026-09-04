# 馬克信箱 故事搜尋站

搜尋《馬克信箱》全部集數裡的**每一封信**，點一下就從那個時間點開始收聽。

《馬克信箱》累積了 900 多集、將近 700 小時。每集有 10–20 封信，但只有其中一封
會出現在標題上，其餘全埋在說明欄的時間戳裡 —— 等於整個 back catalogue 搜不到。
這個站把那些時間戳抽出來，變成可以搜尋的索引。

- **搜尋單位是「一則故事」**，不是「一集」。
- 中文用**子字串比對**而不是分詞，不會有斷詞錯誤；簡體查詢也能命中繁體內容。
- 每則故事都能產生 `https://youtu.be/{id}?t={秒數}` 的連結，點了直接跳到那一刻。
- 純靜態網站，零建置工具，直接放 GitHub Pages。

---

## 首次設定

### 1. 取得 YouTube Data API 金鑰

1. 到 [Google Cloud Console](https://console.cloud.google.com/) 建立一個專案
2. 「API 和服務」→「程式庫」→ 搜尋 **YouTube Data API v3** → 啟用
3. 「API 和服務」→「憑證」→「建立憑證」→「API 金鑰」

把金鑰放進 `.env`（這個檔案已經在 `.gitignore` 裡，不會進版控）：

```
YOUTUBE_API_KEY=你的金鑰
```

> 為什麼要用官方 API：直接爬 YouTube 網頁抓 900 多支影片會觸發 bot 偵測，IP 會被
> 暫時封鎖。官方 API 一次可帶 50 個影片 ID，全部集數只要 19 個請求、約 20 點配額
> （每日免費額度 10,000 點），而且不會被擋。

### 2. 抓資料、建索引

```bash
python fetch_api.py --refresh-list
python build.py
```

### 3. 本機預覽

```bash
python -m http.server 8765 --directory docs
```

開 <http://localhost:8765/search/>。

---

## 每週更新

新集數出來之後：

```bash
python update.py
```

它會重抓頻道清單、只下載還沒抓過的集數、重建索引。接著推上去：

```bash
git add -A && git commit -m "更新索引" && git push
```

> 沒有做成 GitHub Actions 自動更新，是因為 YouTube 會擋資料中心 IP，
> 在 Actions 上跑抓取並不穩定，本機跑反而可靠。

---

## 部署到 GitHub Pages

1. 在 GitHub 建一個新的 repo
2. 推上去：
   ```bash
   git init
   git add -A
   git commit -m "馬克信箱搜尋站"
   git branch -M main
   git remote add origin https://github.com/你的帳號/你的repo.git
   git push -u origin main
   ```
3. repo 的 **Settings → Pages** → Source 選 `Deploy from a branch`，
   branch 選 `main`、資料夾選 **`/docs`** → Save
4. 大約一分鐘後網站會出現在 `https://你的帳號.github.io/你的repo/`

---

## 專案結構

```
chapters.py        從說明欄解析時間戳（唯一的解析邏輯，兩支抓取腳本共用）
fetch_api.py       用 YouTube Data API 抓中繼資料 → cache/
scrape.py          備用：用 yt-dlp 抓（不需 API 金鑰，但會被限速）
build.py           cache/ → docs/search/data/index.json
update.py          fetch_api + build 的組合技

cache/             每集精簡後的中繼資料（gitignore，可重建）
channel_list.json  頻道影片清單
docs/              GitHub Pages 根目錄
  index.html       節目介紹頁（landing，原始檔在 ../dearmarcy-landing/）
  search/          故事搜尋站
    index.html
    app.js         搜尋、播放、連結產生
    style.css
    data/index.json  搜尋索引（約 60KB gzip）
```

### 為什麼 `build.py` 會重新解析說明欄

`cache/` 裡雖然已經存了 chapters，但 `build.py` 一律從 `description` 重新解析。
這樣不管當初是 yt-dlp 還是 Data API 抓的都套用同一套規則，而且日後改進
`chapters.py` 只要重跑 `build.py`，不必重抓 900 多集。

---

## 目前的資料規模

| | |
|---|---|
| 頻道影片總數 | 1,339 |
| 標題含「馬克信箱」 | 933 |
| 實際收錄 | **933 集**（2017–2026） |
| 有分段時間戳 | 681 集（73%） |
| 可搜尋的故事 | **9,706 則** |
| 索引體積 | 554KB，gzip 後 261KB |

各年份的分段覆蓋率差很多，是撰稿習慣造成的：

```
2019  96%   2020  91%   2022  80%   2021  77%   2017  76%
2023  74%   2024  69%   2025  59%   2018  54%   2026  40%
```

## 資料的已知限制

- **約四分之一的集數，說明欄沒有寫時間戳。** 這是撰稿習慣的問題，不是抓取問題。
  那些集數仍會收錄，但只能搜到標題，沒有分段。
- **62 集會員限定的沒有收錄。** 它們根本不在頻道的上傳清單裡，Data API 也拿不到，
  而且一般聽眾本來就看不到。清單仍記在索引的 `skipped` 欄位，但不顯示在站上。
- **年齡限制的集數有收錄**，會標上「年齡限制」badge。訪客要登入 YouTube 且滿 18 歲
  才播得動。這批只有 yt-dlp 抓不到，Data API 正常回傳。

> 順帶一提：Data API 的**上傳清單比 yt-dlp 的頻道 `/videos` 頁完整**，多找到 68 支
> 影片（1,339 vs 1,271）。所以 `update.py` 走 API 不只是為了避開封鎖，資料也更全。
- 章節標題原封不動照用，包含「感謝 @某某」「開場」「星期四見」這類固定段落。
  這些會被標記為罐頭段落、在搜尋排序時降權，但仍然搜得到。

`chapters.py` 的解析規則對齊 yt-dlp，並針對這個頻道的實際寫法放寬了三處：
時間戳後面沒空格（`0:00開場`）、方括號包住（`[00:00] 標題`）、
前面有 emoji（`📝00:00 標題`）。以 223 集 yt-dlp 的結果當標準答案驗證，
一致率 97%，其餘是我們比 yt-dlp 多抓到段落。

---

資料整理自 YouTube 頻道 [上班可以聽 @dearmarcy](https://www.youtube.com/@dearmarcy)
的公開說明欄。本站只是索引與導引，所有內容版權屬原頻道所有。
