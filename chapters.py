#!/usr/bin/env python3
"""
從 YouTube 說明欄解析章節（每封信的時間戳）。

行為對齊 yt-dlp 的 `_extract_chapters_from_description`，這樣不管中繼資料是
yt-dlp 抓的還是 Data API 抓的，結果都一致。

多一個 `lenient` 模式：YouTube 官方要「至少 3 段、第一段必須是 0:00」才會
生成章節，但《馬克信箱》有不少集的說明欄時間戳是從 0:00 以外開始的。那些
時間戳一樣正確、一樣有用，lenient 模式會把它們收下來。
"""
from __future__ import annotations

import re

TS = r"(?:\d+:)?\d{1,2}:\d{2}"

# yt-dlp 原本要求時間戳後面一定要有空白，但《馬克信箱》有不少集寫成
# 「0:00開場」「15:24〖2024 馬曆大特價〗」這種沒空格的形式。
# 所以改成：可有可無的水平空白 + 可有可無的一個連接符號 + 空白。
# 連接符號只列破折號、冒號那幾種，才不會把標題開頭的〖【（吃掉。
# 分隔符只收破折號、冒號與「收尾」括號，這樣「15:24〖2024 馬曆大特價〗」的
# 〖 會留在標題裡，而「[00:00] 標題」的 ] 會被吃掉。
SEP = r"[ \t　]*[-–—~～|｜:：.、\])）】》〕〗]?[ \t　]*"
H = r"[ \t　]*"
# 行首的裝飾符號：📝、🔺、[、▶ 之類。\w 在 Python 是 Unicode-aware，
# 中日韓文字算 \w，所以這個字元類不會咬到標題本文。
LEAD = r"[^\w\n]{0,4}?"

# 時間戳在行首、標題在後（絕大多數）
TS_FIRST = re.compile(rf"(?m)^{H}{LEAD}({TS})(?!\d){SEP}(.+?){H}$")
# 標題在前、時間戳在後（少數集數這樣寫）
TS_LAST = re.compile(rf"(?m)^{H}(.+?){SEP}({TS})(?!\d){H}$")


def parse_ts(t: str) -> int | None:
    parts = t.split(":")
    if not all(p.isdigit() for p in parts):
        return None
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return None


def _collect(pairs: list[tuple[int, str]], duration: int | None) -> list[dict]:
    """套用 yt-dlp 的過濾規則：排序後只留下遞增且不超過片長的時間點。"""
    if not duration:
        return []
    pairs = sorted(pairs, key=lambda p: p[0])
    out: list[dict] = []
    last = 0
    for start, title in pairs:
        title = title.strip()
        if not title:
            continue
        if last <= start <= duration:
            out.append({"start": start, "title": title})
            last = start
    return out


def extract(description: str | None, duration: int | None,
            lenient: bool = True) -> list[dict]:
    """回傳 [{'start': 秒數, 'title': 段落標題}, ...]，找不到就是空 list。"""
    desc = description or ""
    if not duration:
        return []

    for pat, ts_group in ((TS_FIRST, 0), (TS_LAST, 1)):
        pairs = []
        for m in pat.finditer(desc):
            raw_ts = m.group(1) if ts_group == 0 else m.group(2)
            title = m.group(2) if ts_group == 0 else m.group(1)
            sec = parse_ts(raw_ts)
            if sec is not None:
                pairs.append((sec, title))
        got = _collect(pairs, duration)
        if got:
            if not lenient and (len(got) < 3 or got[0]["start"] != 0):
                # 嚴格模式：比照 YouTube 生成章節的門檻
                continue
            return got
    return []
