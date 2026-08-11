#!/usr/bin/env python3
"""
把 cache/ 裡的原始中繼資料，整理成前端要用的單一搜尋索引。

輸出：docs/data/index.json

用法：
    python build.py
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from chapters import extract as extract_chapters

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache"
OUT = ROOT / "docs" / "data" / "index.json"
FAILED_FILE = ROOT / "failed.json"
SKIPPED_FILE = ROOT / "skipped.json"


# --------------------------------------------------------------------------
# 標題解析
#
# 集數命名有好幾種歷史格式，861/931 是標準的「… | 馬克信箱 26w33-1」，
# 其餘是 2017 舊格式與特別場次。依序比對，都不中就當作無集號的特別集。
# --------------------------------------------------------------------------
TITLE_PATTERNS = [
    # 青春點點點 | 馬克信箱 | 2017 Week 53 :: 騙砲!!!!!
    (re.compile(r"^.*?馬克信箱\s*[|｜]\s*(?P<yyyy>\d{4})\s*Week\s*(?P<w>\d{1,2})\s*(?:::)?\s*(?P<t>.*)$",
                re.I), "old"),
    # 標題 | 馬克信箱 26w33-1   /   標題 | 馬克信箱 26w33
    (re.compile(r"^(?P<t>.*?)\s*[|｜]\s*馬克信箱\s*(?P<yy>\d{2})w(?P<w>\d{1,2})"
                r"(?:[-–—](?P<p>\d+))?\s*(?P<tail>.*)$", re.I), "std"),
    # @oldyu X 馬克信箱 22w28-1 | 我要預測幾件事：   （集號在前、標題在後）
    (re.compile(r"^.*?馬克信箱\s*(?P<yy>\d{2})w(?P<w>\d{1,2})(?:[-–—](?P<p>\d+))?"
                r"\s*[|｜]\s*(?P<t>.*)$", re.I), "std"),
    # 2019w44 馬克信箱小劇場   /   有話直說歐直說主持的－馬克信箱 23w10
    (re.compile(r"^(?P<t>.*?)\s*(?P<yy>\d{2})w(?P<w>\d{1,2})(?:[-–—](?P<p>\d+))?\s*(?P<tail>.*)$",
                re.I), "loose"),
]

# 把標題尾巴殘留的節目名／來賓標記清掉
TRAILING_JUNK = re.compile(
    # 「… | 馬克信箱 2022-TICC」這種帶場次後綴的，整段吃掉
    r"\s*[|｜]\s*(馬克信箱|青春點點點|上班可以聽).*$|"
    # 來賓標記。單獨的 X 一定要跟著 @ 才算，否則會誤傷正常標題裡的 x
    r"\s*(ft\.|feat\.)\s*@?\S.*$|"
    r"\s+X\s+@\S.*$",
    re.I,
)


def parse_title(raw: str) -> tuple[str, str | None, int | None]:
    """回傳 (乾淨標題, 集號標籤如 '26w33-1', 集號年份如 2026)。"""
    raw = raw.strip()
    for pat, kind in TITLE_PATTERNS:
        m = pat.match(raw)
        if not m:
            continue
        g = m.groupdict()
        if kind == "old":
            year = int(g["yyyy"])
            label = f"{year}w{int(g['w']):02d}"
        else:
            yy = int(g["yy"])
            # 兩位數年份：18–26 → 2018–2026。>26 視為誤判（例如標題裡的其他數字）
            if not 17 <= yy <= 30:
                continue
            year = 2000 + yy
            label = f"{yy}w{int(g['w']):02d}"
            if g.get("p"):
                label += f"-{g['p']}"
        title = (g.get("t") or "").strip()
        return title, label, year
    return raw, None, None


def clean_title(t: str) -> str:
    prev = None
    while prev != t:
        prev = t
        t = TRAILING_JUNK.sub("", t).strip()
    return t.strip(" |｜-–—:：")


# --------------------------------------------------------------------------
# 章節處理
# --------------------------------------------------------------------------
# yt-dlp 對沒標題的章節會自動命名，這種完全沒有搜尋價值，直接丟掉
UNTITLED = re.compile(r"^<untitled chapter\s*\d*>$", re.I)

# 這些是每集固定的開場／收尾／贊助段落。照樣收錄可搜尋，但排序降權，
# 否則搜「感謝」會被幾百則「感謝@某某」洗版。
BOILERPLATE = re.compile(
    r"^感謝|^開場|^片頭|^前言|^本集開始|^開始囉|^哈囉|^hello|"
    r"星期[一二三四五六日天]見|^下[週周]見|^結尾|^結語|^收尾|^ending|"
    r"^廣告|業配|^贊助|團購|折扣碼|優惠碼|^工商|"
    r"^會員|^訂閱|^片尾|^彩蛋預告|^目錄君|^本週目錄",
    re.I,
)


def process_chapters(chapters: list[dict], duration: int | None) -> list[list]:
    out = []
    for c in chapters:
        title = (c.get("title") or "").strip()
        if not title or UNTITLED.match(title):
            continue
        start = int(c.get("start") or 0)
        if duration and start >= duration:
            continue
        out.append([start, title, 1 if BOILERPLATE.search(title) else 0])
    out.sort(key=lambda x: x[0])
    return out


# --------------------------------------------------------------------------
# 繁→簡對照表
#
# 只針對語料裡真的出現、且繁簡不同的字產生對照，表就會很小（通常 1–2KB）。
# 前端載入後把語料與查詢都正規化成簡體再比對，簡體查詢就能命中繁體內容。
# --------------------------------------------------------------------------
def build_t2s_map(corpus: str) -> list[str]:
    """回傳 [繁體字串, 對應的簡體字串] 兩條等長（以 code point 計）的平行字串。

    不能存成「繁簡繁簡…」相接的單一字串再用 index 每兩個字讀一組：
    Python 的 len() 算 code point，JavaScript 的字串索引算 UTF-16 單位，
    只要有一個字落在 BMP 之外，JS 那邊就會從該處整串錯位。
    分成兩條平行字串、前端用 Array.from 依 code point 拆，就不會有這個問題。
    """
    try:
        from opencc import OpenCC
    except ImportError:
        print("  ! 找不到 opencc，跳過繁簡對照（簡體查詢將無法命中）")
        return ["", ""]
    cc = OpenCC("t2s")
    trad, simp = [], []
    for ch in sorted(set(corpus)):
        if not "一" <= ch <= "鿿":
            continue
        s = cc.convert(ch)
        if len(s) == 1 and s != ch:
            trad.append(ch)
            simp.append(s)
    return ["".join(trad), "".join(simp)]


# --------------------------------------------------------------------------
# 靜態資源的版本戳
#
# GitHub Pages 會讓瀏覽器快取 app.js / style.css，改版之後回訪的人可能還在跑
# 舊程式。把內容雜湊寫進 index.html 的 ?v=，內容沒變就不會變，變了就一定失效。
# --------------------------------------------------------------------------
def stamp_assets() -> None:
    import hashlib

    html_path = OUT.parent.parent / "index.html"
    if not html_path.exists():
        return
    html = html_path.read_text(encoding="utf-8")
    changed = False
    for name in ("app.js", "style.css"):
        f = html_path.parent / name
        if not f.exists():
            continue
        digest = hashlib.sha256(f.read_bytes()).hexdigest()[:8]
        new, n = re.subn(rf'({re.escape(name)})\?v=[^"\']*', rf"\1?v={digest}", html)
        if n:
            changed = changed or new != html
            html = new
    if changed:
        html_path.write_text(html, encoding="utf-8")
        print("  已更新 index.html 的資源版本戳")


# --------------------------------------------------------------------------
def main() -> int:
    files = sorted(CACHE.glob("*.json"))
    if not files:
        print("cache/ 是空的，請先執行 python scrape.py")
        return 1

    eps = []
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        raw_title = d.get("title") or ""
        title, label, ep_year = parse_title(raw_title)
        title = clean_title(title)

        # 一律從說明欄重新解析，不用 cache 裡既有的 chapters。
        # 這樣不管當初是 yt-dlp 還是 Data API 抓的，都套用同一套規則；
        # 之後改進 chapters.py 也只要重跑 build.py，不必重抓。
        raw_chapters = extract_chapters(d.get("description"), d.get("duration"))
        chapters = process_chapters(raw_chapters, d.get("duration"))
        upload = d.get("upload_date") or ""

        # 少數集數標題是空的（例如「| 馬克信箱 20w23」），拿第一個非罐頭章節頂替
        if not title:
            real = [c[1] for c in chapters if not c[2]]
            title = real[0] if real else (label or raw_title or "(無標題)")

        rec = {
            "i": d["id"],
            "t": title,
            "d": upload,
            "s": int(d.get("duration") or 0),
            "n": label or "",
            "y": ep_year or (int(upload[:4]) if upload[:4].isdigit() else 0),
            "c": chapters,
        }
        # 年齡限制的集數要標記：訪客得先登入 YouTube 才播得動
        if d.get("age_restricted"):
            rec["r"] = 1
        eps.append(rec)

    # 新到舊
    eps.sort(key=lambda e: (e["d"], e["n"]), reverse=True)

    corpus = "".join(e["t"] + "".join(c[1] for c in e["c"]) for e in eps)
    t2s = build_t2s_map(corpus)

    # 未收錄的集數有兩個來源：yt-dlp 的 failed.json、Data API 的 skipped.json
    skipped = []
    if FAILED_FILE.exists():
        failed = json.loads(FAILED_FILE.read_text(encoding="utf-8"))
        skipped += [{"i": vid, "t": f["title"], "why": f["kind"]}
                    for vid, f in failed.items() if f["kind"] in ("members", "age")]
    if SKIPPED_FILE.exists():
        have = {s["i"] for s in skipped}
        skipped += [{"i": s["id"], "t": s["title"], "why": "members"}
                    for s in json.loads(SKIPPED_FILE.read_text(encoding="utf-8"))
                    if s["id"] not in have]
    cached = {f.stem for f in files}
    skipped = [s for s in skipped if s["i"] not in cached]

    payload = {
        "generated": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d"),
        "t2s": t2s,
        "skipped": skipped,
        "eps": eps,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")

    stamp_assets()

    # ---------------- 統計 ----------------
    import gzip
    raw_kb = OUT.stat().st_size / 1024
    gz_kb = len(gzip.compress(OUT.read_bytes(), 6)) / 1024
    total_ch = sum(len(e["c"]) for e in eps)
    real_ch = sum(1 for e in eps for c in e["c"] if not c[2])
    with_ch = sum(1 for e in eps if e["c"])
    no_label = sum(1 for e in eps if not e["n"])

    print(f"\n索引輸出 → {OUT.relative_to(ROOT)}")
    print(f"  集數 {len(eps)}｜有章節 {with_ch} ({with_ch/len(eps)*100:.0f}%)｜無集號 {no_label}")
    print(f"  章節 {total_ch}（可搜尋故事 {real_ch}、罐頭段落 {total_ch-real_ch}）")
    print(f"  繁簡對照 {len(t2s[0])} 字")
    print(f"  體積 {raw_kb:.0f}KB，gzip 後 {gz_kb:.0f}KB")

    by_year: dict[int, list[int]] = {}
    for e in eps:
        y = e["y"]
        by_year.setdefault(y, [0, 0])
        by_year[y][0] += 1
        by_year[y][1] += len(e["c"])
    print("\n  年份    集數   章節   有章節比例")
    for y in sorted(by_year):
        n, ch = by_year[y]
        cov = sum(1 for e in eps if e["y"] == y and e["c"]) / n * 100
        print(f"  {y}   {n:4d}  {ch:5d}   {cov:5.0f}%")

    if skipped:
        kinds: dict[str, int] = {}
        for s in skipped:
            kinds[s["why"]] = kinds.get(s["why"], 0) + 1
        print(f"\n  未收錄 {len(skipped)} 集：{kinds}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
