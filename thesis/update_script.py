"""Rebuild the 使用方式 + 逐頁講稿 sections of 期中報告講稿_5分鐘.md from the deck's speaker notes.

Usage (from the repo root, after `node thesis/build_midterm.js ...`):
    .venv/Scripts/python.exe thesis/update_script.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from pptx import Presentation

HERE = Path(__file__).parent
DECK = HERE / "最佳化方法_期中報告_M1527488.pptx"
MD = HERE / "期中報告講稿_5分鐘.md"

notes = [[i + 1, "", s.notes_slide.notes_text_frame.text] for i, s in enumerate(Presentation(DECK).slides)]
with tempfile.TemporaryDirectory() as tmp:
    nj, part = Path(tmp) / "notes.json", Path(tmp) / "part.md"
    nj.write_text(json.dumps(notes, ensure_ascii=False), encoding="utf-8")
    out = subprocess.run([sys.executable, str(HERE / "script_md.py"), str(nj), str(part)],
                         capture_output=True, text=True, encoding="utf-8",
                         env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    if out.returncode or "KEY MISSING" in out.stdout:
        sys.exit(out.stdout + out.stderr)
    text = MD.read_text(encoding="utf-8")
    a, b = text.index("## 使用方式"), text.index("## 可能被問的問題")
    MD.write_text(text[:a] + part.read_text(encoding="utf-8").rstrip() + "\n\n" + text[b:], encoding="utf-8")
print("updated", MD.name)
