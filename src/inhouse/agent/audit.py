"""감사 로그: 누가·어떤 질문으로·어떤 SQL을·결과가 어땠는지. 답의 출처(쿼리 ID)가 이 기록을 가리킨다."""
from __future__ import annotations

import json
import os
import threading
from typing import Any


class MemoryAudit:
    def __init__(self):
        self.records: list[dict[str, Any]] = []

    def write(self, record: dict[str, Any]) -> None:
        self.records.append(record)


class JsonlAudit:
    """한 줄에 한 기록(JSON). 관측 도구로 그대로 보낼 수 있는 형식."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    def write(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
