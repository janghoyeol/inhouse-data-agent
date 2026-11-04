"""사내 문서(2단계, ADR 0004): 마크다운 읽기 → 절 단위 조각 → 검색용 tsvector.

검색어는 한국어를 2글자씩 겹쳐 자르고(형태소 분석기 없이 조사·어미가 붙어도 걸리게), 영문·숫자는 단어 그대로 쓴다.
Postgres 'simple' 파서는 한글·하이픈 토큰을 제멋대로 쪼개므로, 파이썬이 만든 토큰을 tsvector 문자열로 직접 넣는다.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from importlib import resources

import yaml

_FRONT = re.compile(r"^---\n(.*?)\n---\n", re.S)
_APPROVAL = re.compile(r"본사 승인 번호:\s*\**(APR-\d{4}-\d{3})")
_TOKEN = re.compile(r"[가-힣]+|[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*")


@dataclass
class Chunk:
    chunk_id: str
    sec_no: int
    heading: str
    content: str


@dataclass
class Doc:
    doc_id: str
    version: int
    title: str
    owner: str
    status: str
    eff_from: dt.date
    eff_to: dt.date | None
    supersedes: str | None
    audience: list[str]
    approval_no: str | None
    body: str
    chunks: list[Chunk] = field(default_factory=list)

    @property
    def doc_key(self) -> str:
        return f"{self.doc_id}@v{self.version}"


def tokens(text: str) -> list[str]:
    out: list[str] = []
    for m in _TOKEN.finditer(text):
        w = m.group(0)
        if "가" <= w[0] <= "힣":
            out.extend([w] if len(w) <= 2 else [w[i:i + 2] for i in range(len(w) - 1)])
        else:
            out.append(w.lower())
    return out


def tsvector_literal(toks: list[str]) -> str:
    """토큰 → tsvector 문자열 ('어휘':위치,...). 따옴표는 두 번 써서 escape."""
    pos: dict[str, list[int]] = {}
    for i, t in enumerate(toks, 1):
        pos.setdefault(t, []).append(min(i, 16383))
    return " ".join("'" + t.replace("'", "''") + "':" + ",".join(map(str, p)) for t, p in pos.items())


def _chunks(doc_key: str, body: str) -> list[Chunk]:
    parts = re.split(r"^## +(.+)$", body, flags=re.M)
    pre = re.sub(r"^# .*$", "", parts[0], flags=re.M).strip()   # 제목 줄 빼고 첫 절 앞의 글 (승인 번호·초안 경고)
    out = [Chunk(f"{doc_key}#0", 0, "머리말", pre)] if pre else []
    for i in range(1, len(parts), 2):
        out.append(Chunk(f"{doc_key}#{len(out) + (0 if pre else 1)}", (i + 1) // 2, parts[i].strip(), parts[i + 1].strip()))
    return out


def parse(text: str) -> Doc:
    m = _FRONT.match(text)
    if not m:
        raise ValueError("머리말(---)이 없습니다")
    h, body = yaml.safe_load(m.group(1)), text[m.end():]
    ap = _APPROVAL.search(body)
    d = Doc(doc_id=h["doc_id"], version=int(h["version"]), title=h["title"], owner=h["owner"], status=h["status"],
            eff_from=h["effective_from"], eff_to=h.get("effective_to"), supersedes=h.get("supersedes"),
            audience=list(h["audience"]), approval_no=ap.group(1) if ap else None, body=body.strip())
    d.chunks = _chunks(d.doc_key, d.body)
    return d


def read_corpus() -> list[Doc]:
    root = resources.files("inhouse.data").joinpath("docs")
    return sorted((parse(p.read_text(encoding="utf-8")) for p in root.iterdir() if p.name.endswith(".md")),
                  key=lambda d: d.doc_key)


def search_text(d: Doc, c: Chunk) -> str:
    """조각 검색어: 문서 제목·번호·절 제목을 같이 넣는다 (절 본문에 '환불'이 없어도 환불 규정의 조각이면 걸리게)."""
    return f"{d.title} {d.doc_id} {c.heading}\n{c.content}"


def load_docs(cur, corpus: list[Doc] | None = None) -> tuple[int, int]:
    corpus = read_corpus() if corpus is None else corpus
    n = 0
    for d in corpus:
        cur.execute("INSERT INTO doc (doc_key, doc_id, version, title, owner, status, eff_from, eff_to, supersedes, "
                    "approval_no, audience, body) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (d.doc_key, d.doc_id, d.version, d.title, d.owner, d.status, d.eff_from, d.eff_to, d.supersedes,
                     d.approval_no, d.audience, d.body))
        for c in d.chunks:
            cur.execute("INSERT INTO doc_chunk (chunk_id, doc_key, sec_no, heading, content, tsv) "
                        "VALUES (%s,%s,%s,%s,%s,%s::tsvector)",
                        (c.chunk_id, d.doc_key, c.sec_no, c.heading, c.content,
                         tsvector_literal(tokens(search_text(d, c)))))
            n += 1
    return len(corpus), n
