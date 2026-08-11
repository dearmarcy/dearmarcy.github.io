#!/usr/bin/env python3
"""
每週更新：抓新集數 → 重建索引。

    python update.py

跑完之後 git commit + push，GitHub Pages 會自動更新。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent


def run(script: str, *args: str) -> None:
    print(f"\n{'=' * 56}\n  {script} {' '.join(args)}\n{'=' * 56}")
    r = subprocess.run([sys.executable, str(ROOT / script), *args], cwd=ROOT)
    if r.returncode != 0:
        sys.exit(f"{script} 失敗（exit {r.returncode}）")


if __name__ == "__main__":
    # --refresh-list 會重抓頻道清單，才找得到新上傳的集數
    run("fetch_api.py", "--refresh-list")
    run("build.py")
    print("\n完成。接著把變更推上去：")
    print("  git add -A && git commit -m \"更新索引\" && git push")
