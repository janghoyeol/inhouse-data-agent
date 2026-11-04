"""문서 인용 검증 (2단계 2a-3, ADR 0004 결정 5).

답 속 문서 인용 형식: [조각ID "인용 문장"]  예) [POL-REF-001@v2#3 "다음 날부터 15일 이내"]
코드가 확인하는 것
- retrieved: 그 조각을 이번 답에서 search_docs/read_doc으로 실제로 받았는가 (= 그 직원 권한으로 보이는 조각)
- quote_found: 인용 문장이 그 조각 본문에 그대로 있는가 (마크다운 강조·공백 차이는 무시, '…'로 줄인 건 순서대로)
- warnings: draft(초안 인용) · superseded(대체된 판 — 적용 기준일 확인 필요) · unapproved_notice(승인 번호 없는 권역 공지)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_ID = r"[A-Z][A-Z0-9-]*@v\d+#\d+"
_Q = r"[\"“]((?:(?![\"”]\s*\]).)+?)[\"”]"   # 인용 안에 따옴표가 있어도 끝의 '"]'까지 읽는다
# [조각ID "인용"] 이 정식. 순서를 뒤집은 ["인용" 조각ID]도 읽는다 (모델이 가끔 그렇게 쓴다)
CITE = re.compile(rf"\[(?:({_ID})(?:\s+{_Q})?|{_Q}\s+({_ID}))\s*\]")
_ELLIPSIS = re.compile(r"\s*(?:…|\.\.\.)\s*")


@dataclass
class Citation:
    chunk_id: str
    quote: str | None


def parse(text: str) -> list[Citation]:
    return [Citation(m.group(1) or m.group(4), ((m.group(2) or m.group(3)) or "").strip() or None)
            for m in CITE.finditer(text or "")]


def _norm(s: str) -> str:
    s = re.sub(r"[*`_\\]", "", s)
    s = s.replace("“", '"').replace("”", '"').replace("’", "'")
    return re.sub(r"\s+", " ", s).strip()


def quote_in(quote: str, content: str) -> bool:
    hay, pos = _norm(content), 0
    for part in (p for p in _ELLIPSIS.split(_norm(quote)) if p):
        i = hay.find(part, pos)
        if i < 0:
            return False
        pos = i + len(part)
    return pos > 0


def verify(text: str, chunks: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """chunks: 이번 답에서 도구로 받은 조각 (chunk_id → 조각 정보)."""
    out = []
    for c in parse(text):
        ch = chunks.get(c.chunk_id)
        r: dict[str, Any] = {"chunk_id": c.chunk_id, "quote": c.quote, "retrieved": ch is not None,
                             "quote_found": None, "warnings": []}
        if ch is not None:
            if c.quote:
                r["quote_found"] = quote_in(c.quote, ch["content"])
            if ch.get("status") in ("draft", "superseded"):
                r["warnings"].append(ch["status"])
            if str(ch.get("doc_id", "")).startswith("NOTICE-") and not ch.get("approval_no"):
                r["warnings"].append("unapproved_notice")
        out.append(r)
    return out


def summary(results: list[dict[str, Any]]) -> dict[str, int]:
    ok = sum(1 for r in results if r["retrieved"] and r["quote_found"])
    return {"citations": len(results), "verified": ok, "unverified": len(results) - ok,
            "warnings": sum(1 for r in results if r["warnings"])}
