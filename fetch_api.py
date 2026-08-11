#!/usr/bin/env python3
"""
用 YouTube Data API v3 抓取「馬克信箱」全部集數的中繼資料。

比 yt-dlp 快兩個數量級，也不會觸發 YouTube 的 bot 偵測：
每次請求可帶 50 個影片 ID，931 集只需要約 19 個請求、約 20 點配額
（每日免費配額 10,000 點）。

API 金鑰讀取順序：--key 參數 → 環境變數 YOUTUBE_API_KEY → 同目錄的 .env

用法：
    python fetch_api.py                # 抓所有還沒進 cache 的集數
    python fetch_api.py --refresh-list # 順便重抓頻道清單（找新集數）
    python fetch_api.py --all          # 強制重抓全部（說明欄有更新時用）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from chapters import extract as extract_chapters

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache"
LIST_FILE = ROOT / "channel_list.json"
SKIPPED_FILE = ROOT / "skipped.json"

API = "https://www.googleapis.com/youtube/v3"
HANDLE = "dearmarcy"
SERIES_MARK = "馬克信箱"

ISO_DUR = re.compile(r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def load_key(cli_key: str | None) -> str:
    if cli_key:
        return cli_key.strip()
    if os.environ.get("YOUTUBE_API_KEY"):
        return os.environ["YOUTUBE_API_KEY"].strip()
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("YOUTUBE_API_KEY"):
                return line.split("=", 1)[1].strip().strip("'\"")
    sys.exit("找不到 API 金鑰。請用 --key 傳入，或在 .env 寫 YOUTUBE_API_KEY=...")


def api(endpoint: str, key: str, **params) -> dict:
    params["key"] = key
    url = f"{API}/{endpoint}?{urllib.parse.urlencode(params)}"
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code in (403, 429):
                try:
                    reason = json.loads(body)["error"]["errors"][0]["reason"]
                except Exception:  # noqa: BLE001
                    reason = body[:200]
                if reason in ("quotaExceeded", "dailyLimitExceeded"):
                    sys.exit(f"API 配額用完了（{reason}）。明天太平洋時間午夜會重置。")
                sys.exit(f"API 拒絕請求：{reason}\n"
                         f"請確認金鑰已啟用 YouTube Data API v3，且沒有設定過嚴的來源限制。")
            if e.code >= 500 and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            sys.exit(f"API 錯誤 {e.code}：{body[:300]}")
        except Exception as e:  # noqa: BLE001
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError("unreachable")


def parse_duration(iso: str | None) -> int:
    if not iso:
        return 0
    m = ISO_DUR.match(iso)
    if not m:
        return 0
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def fetch_channel_list(key: str) -> list[dict]:
    """抓頻道上傳清單的全部影片。"""
    ch = api("channels", key, part="contentDetails,snippet", forHandle=HANDLE)
    items = ch.get("items") or []
    if not items:
        sys.exit(f"找不到頻道 @{HANDLE}")
    uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    name = items[0]["snippet"]["title"]
    print(f"頻道：{name}（上傳清單 {uploads}）")

    vids, token, page = [], None, 0
    while True:
        page += 1
        r = api("playlistItems", key, part="snippet,contentDetails",
                playlistId=uploads, maxResults=50, **({"pageToken": token} if token else {}))
        for it in r.get("items", []):
            sn = it["snippet"]
            vids.append({
                "id": it["contentDetails"]["videoId"],
                "title": sn.get("title") or "",
                "duration": None,
            })
        token = r.get("nextPageToken")
        print(f"\r  已取得 {len(vids)} 支…", end="", flush=True)
        if not token:
            break
    print()
    LIST_FILE.write_text(json.dumps(vids, ensure_ascii=False, indent=1), encoding="utf-8")
    return vids


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=None)
    ap.add_argument("--refresh-list", action="store_true")
    ap.add_argument("--all", action="store_true", help="忽略 cache，全部重抓")
    args = ap.parse_args()

    key = load_key(args.key)
    CACHE.mkdir(exist_ok=True)

    if args.refresh_list or not LIST_FILE.exists():
        videos = fetch_channel_list(key)
    else:
        videos = json.loads(LIST_FILE.read_text(encoding="utf-8"))
    print(f"頻道共 {len(videos)} 支影片")

    series = [v for v in videos if SERIES_MARK in v["title"]]
    print(f"其中「{SERIES_MARK}」{len(series)} 集")

    todo = series if args.all else [v for v in series
                                    if not (CACHE / f"{v['id']}.json").exists()]
    print(f"cache 已有 {len(series) - len(todo)} 集，本次要抓 {len(todo)} 集")
    if not todo:
        print("沒有要抓的東西。")
        return 0

    ids = [v["id"] for v in todo]
    got, missing, total_ch, with_ch = 0, [], 0, 0
    started = time.time()

    for i in range(0, len(ids), 50):
        batch = ids[i:i + 50]
        r = api("videos", key, part="snippet,contentDetails,statistics", id=",".join(batch))
        returned = set()

        for it in r.get("items", []):
            vid = it["id"]
            returned.add(vid)
            sn = it["snippet"]
            desc = sn.get("description") or ""
            dur = parse_duration(it.get("contentDetails", {}).get("duration"))
            chapters = extract_chapters(desc, dur)

            (CACHE / f"{vid}.json").write_text(json.dumps({
                "id": vid,
                "title": sn.get("title") or "",
                "description": desc,
                "duration": dur,
                "upload_date": (sn.get("publishedAt") or "")[:10].replace("-", ""),
                "view_count": int(it.get("statistics", {}).get("viewCount") or 0),
                "age_restricted": it.get("contentDetails", {})
                                    .get("contentRating", {}).get("ytRating") == "ytAgeRestricted",
                "chapters": chapters,
                "scraped_at": int(time.time()),
            }, ensure_ascii=False), encoding="utf-8")

            got += 1
            total_ch += len(chapters)
            with_ch += 1 if chapters else 0

        # API 不會回傳私人／會員限定的影片，它們就是差集
        for vid in batch:
            if vid not in returned:
                title = next((v["title"] for v in todo if v["id"] == vid), "")
                missing.append({"id": vid, "title": title})

        print(f"\r  {min(i + 50, len(ids))}/{len(ids)}　"
              f"成功 {got}、有章節 {with_ch}、章節數 {total_ch}", end="", flush=True)

    print(f"\n\n完成：{got} 集，耗時 {time.time() - started:.1f} 秒")
    print(f"  有章節 {with_ch} 集（{with_ch / max(got, 1) * 100:.0f}%）、共 {total_ch} 個章節")
    print(f"  用掉約 {len(ids) // 50 + 1} 點配額（每日 10,000 點）")

    if missing:
        SKIPPED_FILE.write_text(json.dumps(missing, ensure_ascii=False, indent=1),
                                encoding="utf-8")
        print(f"  {len(missing)} 集 API 未回傳（會員限定或已下架）→ {SKIPPED_FILE.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
