#!/usr/bin/env python3
"""
抓取「馬克信箱」全部集數的中繼資料與章節（每封信的 timecode）。

資料來源：YouTube 頻道 @dearmarcy
每集說明欄都有逐封信的時間戳，YouTube 會解析成 chapters，這支腳本就是把它們撈下來。

用法：
    python scrape.py                 # 抓全部（可中斷續跑）
    python scrape.py --limit 20      # 只抓 20 支，用來驗證
    python scrape.py --cookies chrome --only-failed age
                                     # 用瀏覽器 cookies 補抓年齡限制的集數
    python scrape.py --refresh-list  # 強制重抓頻道清單（找新集數用）
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache"
CHANNEL = "https://www.youtube.com/@dearmarcy/videos"
LIST_FILE = ROOT / "channel_list.json"
FAILED_FILE = ROOT / "failed.json"

# 只收標題含這個字串的影片（排除青春點點點、英文版、旅遊開箱等）
SERIES_MARK = "馬克信箱"

# 我們只留下需要的欄位，完整 info.json 每支有數百 KB，931 支會爆掉
KEEP = ("id", "title", "description", "duration", "upload_date", "view_count")


# --------------------------------------------------------------------------
# 失敗分類
# --------------------------------------------------------------------------
def classify(msg: str) -> str:
    m = msg.lower()
    if "confirm your age" in m or "age-restricted" in m:
        return "age"          # 年齡限制 → 需要 cookies
    if "members" in m and "level" in m:
        return "members"      # 會員限定 → 一般聽眾本來就看不到，跳過
    if "private" in m:
        return "private"
    if "unavailable" in m or "removed" in m or "terminated" in m:
        return "unavailable"
    if ("429" in m or "too many requests" in m
            or "not a bot" in m or "sign in to confirm you" in m):
        # 「confirm you're not a bot」是暫時性的頻率封鎖，冷卻後就會恢復，
        # 必須當成限速重試，不能當成永久失敗
        return "ratelimit"
    return "other"


# --------------------------------------------------------------------------
# 節流：遇到 429 就讓所有 worker 一起停一下，而不是各自硬撞
# --------------------------------------------------------------------------
class Throttle:
    def __init__(self, base_delay: float = 0.25):
        self.base = base_delay
        self.gate = threading.Event()
        self.gate.set()
        self.lock = threading.Lock()
        self.penalty = 0.0

    def wait(self) -> None:
        self.gate.wait()
        time.sleep(self.base + self.penalty + random.uniform(0, 0.25))

    def hit_429(self) -> None:
        with self.lock:
            if not self.gate.is_set():
                return                      # 已經有人在冷卻了
            self.penalty = min(self.penalty * 2 + 0.5, 8.0)
            cool = min(20 + self.penalty * 5, 90)
            print(f"  ! 觸發 429，全體暫停 {cool:.0f}s（後續每支加 {self.penalty:.1f}s）", flush=True)
            self.gate.clear()
        time.sleep(cool)
        self.gate.set()


# --------------------------------------------------------------------------
# 頻道清單
# --------------------------------------------------------------------------
def fetch_channel_list(refresh: bool = False) -> list[dict]:
    if LIST_FILE.exists() and not refresh:
        return json.loads(LIST_FILE.read_text(encoding="utf-8"))

    print("抓取頻道影片清單…（約 1–2 分鐘）", flush=True)
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(CHANNEL, download=False)

    entries = info.get("entries") or []
    vids = [
        {"id": e["id"], "title": e.get("title") or "", "duration": e.get("duration")}
        for e in entries
        if e and e.get("id")
    ]
    LIST_FILE.write_text(json.dumps(vids, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"頻道共 {len(vids)} 支影片 → {LIST_FILE.name}", flush=True)
    return vids


# --------------------------------------------------------------------------
# 單支抓取
# --------------------------------------------------------------------------
def fetch_one(vid: str, ydl: YoutubeDL, throttle: Throttle, retries: int = 5):
    """回傳 (slim_dict, None) 或 (None, (kind, message))。"""
    for attempt in range(retries):
        throttle.wait()
        try:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
        except DownloadError as e:
            kind = classify(str(e))
            if kind == "ratelimit" and attempt < retries - 1:
                throttle.hit_429()
                continue
            if kind == "other" and attempt < retries - 1:
                time.sleep(2 + attempt * 3)
                continue
            return None, (kind, str(e)[:300])
        except Exception as e:  # noqa: BLE001 - 網路層各種例外都當可重試
            if attempt < retries - 1:
                time.sleep(2 + attempt * 3)
                continue
            return None, ("other", f"{type(e).__name__}: {e}"[:300])

        slim = {k: info.get(k) for k in KEEP}
        slim["chapters"] = [
            {"start": int(c.get("start_time") or 0), "title": (c.get("title") or "").strip()}
            for c in (info.get("chapters") or [])
        ]
        slim["scraped_at"] = int(time.time())
        return slim, None

    return None, ("other", "retries exhausted")


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只抓前 N 支（測試用）")
    ap.add_argument("--workers", type=int, default=3, help="並行數，預設 3")
    ap.add_argument("--cookies", default=None,
                    help="瀏覽器名稱，如 chrome / edge / firefox（補抓年齡限制集數用）")
    ap.add_argument("--only-failed", default=None,
                    help="只重抓 failed.json 裡指定類別，如 age")
    ap.add_argument("--refresh-list", action="store_true", help="強制重抓頻道清單")
    args = ap.parse_args()

    CACHE.mkdir(exist_ok=True)

    videos = fetch_channel_list(refresh=args.refresh_list)
    series = [v for v in videos if SERIES_MARK in v["title"]]
    print(f"其中「{SERIES_MARK}」{len(series)} 集", flush=True)

    failed: dict[str, dict] = {}
    if FAILED_FILE.exists():
        failed = json.loads(FAILED_FILE.read_text(encoding="utf-8"))

    if args.only_failed:
        wanted = {vid for vid, f in failed.items() if f["kind"] == args.only_failed}
        series = [v for v in series if v["id"] in wanted]
        print(f"只重抓 kind={args.only_failed} 的 {len(series)} 集", flush=True)

    todo = [v for v in series if not (CACHE / f"{v['id']}.json").exists()]
    if args.limit:
        todo = todo[: args.limit]

    done_already = len(series) - len([v for v in series if not (CACHE / f"{v['id']}.json").exists()])
    print(f"cache 已有 {done_already} 集，本次要抓 {len(todo)} 集", flush=True)
    if not todo:
        print("沒有要抓的東西。")
        return 0

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "ignoreerrors": False,
        "socket_timeout": 30,
    }
    if args.cookies:
        opts["cookiesfrombrowser"] = (args.cookies,)
        print(f"使用 {args.cookies} 的登入 cookies", flush=True)

    throttle = Throttle()
    counter = {"ok": 0, "fail": 0, "chapters": 0}
    lock = threading.Lock()
    started = time.time()
    total = len(todo)

    def work(item):
        vid = item["id"]
        with YoutubeDL(opts) as ydl:
            slim, err = fetch_one(vid, ydl, throttle)

        with lock:
            if err:
                counter["fail"] += 1
                failed[vid] = {"kind": err[0], "title": item["title"], "msg": err[1]}
                tag = f"✗ {err[0]}"
            else:
                (CACHE / f"{vid}.json").write_text(
                    json.dumps(slim, ensure_ascii=False), encoding="utf-8"
                )
                failed.pop(vid, None)
                counter["ok"] += 1
                counter["chapters"] += len(slim["chapters"])
                tag = f"✓ {len(slim['chapters']):2d} 段"

            n = counter["ok"] + counter["fail"]
            if n % 10 == 0 or n == total or err:
                rate = n / max(time.time() - started, 1e-6)
                eta = (total - n) / rate if rate else 0
                print(f"[{n:4d}/{total}] {tag}  {item['title'][:34]:36s} "
                      f"| {rate:.1f}/s ETA {eta/60:.0f}分", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(work, todo))

    FAILED_FILE.write_text(json.dumps(failed, ensure_ascii=False, indent=1), encoding="utf-8")

    mins = (time.time() - started) / 60
    print(f"\n完成：成功 {counter['ok']}、失敗 {counter['fail']}、"
          f"共 {counter['chapters']} 個章節，耗時 {mins:.1f} 分")

    if failed:
        kinds: dict[str, int] = {}
        for f in failed.values():
            kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
        print("失敗分類：", kinds, f"→ 明細見 {FAILED_FILE.name}")
        if kinds.get("age"):
            print("  年齡限制的可用：python scrape.py --cookies chrome --only-failed age")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
