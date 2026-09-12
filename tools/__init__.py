"""調査・運用の道具。`uv run python -m tools.<name>` で実行する。

ここで stdout を UTF-8 にしている。Windows の既定（cp932）のままだと、ページに含まれる
記号（♨ など）を印字した瞬間に `UnicodeEncodeError` で**処理そのものが落ちる**。
400 リクエストの発見処理が印字で落ちて結果を失ったことがあるので、入口で直しておく。
"""

import sys

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="replace")
