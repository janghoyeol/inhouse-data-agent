"""Tucanova 데이터 에이전트 웹 화면 (6단계).

- 묻는 사람은 정해진 직원 역할(PERSONAS) 중에서만 고른다. 임의의 DB 계정 이름은 받지 않는다.
- 답은 에이전트(agent.ask)가 직원 역할로 DB를 조회해 만들고, 쓴 SQL과 결과·쿼리 ID를 같이 돌려준다.
- 공개 데모를 가정해 하루(KST) Claude 비용 상한과 IP별 질문 수 상한(분당·하루)을 둔다.
- 감사 로그 API(/api/audit)는 다른 방문자의 질문이 보이므로 ENABLE_AUDIT_API=1일 때만 켠다.
- 환불 제안·승인(2단계 2b)은 실제 환불 기록을 만들므로 ENABLE_APPROVALS=1일 때만 켠다 (에이전트 제안 도구 + /approvals 승인 화면).

실행: DATABASE_URL=… TCN_USER_PASSWORD=… ANTHROPIC_API_KEY=… uvicorn inhouse.web.app:app
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
from collections import deque
from importlib import resources
from typing import Any, Callable
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ..agent import agent as agent_mod
from ..agent import refunds
from ..agent.audit import JsonlAudit
from ..agent.personas import PERSONAS
from ..agent.tools import Toolbox
from ..data.transform import REGIONS

KST = ZoneInfo("Asia/Seoul")
MAX_QUESTION = 500
MAX_ROWS_SHOWN = 20


class Ask(BaseModel):
    db_user: str
    question: str


class Decide(BaseModel):
    db_user: str
    approve: bool
    note: str | None = None


class CostGuard:
    """하루(KST) Claude 비용 상한. 넘으면 그날은 더 묻지 않는다."""

    def __init__(self, cap_usd: float, clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(KST)):
        self.cap, self.clock = cap_usd, clock
        self.day, self.spent = None, 0.0
        self._lock = threading.Lock()

    def _roll(self) -> None:
        today = self.clock().date()
        if today != self.day:
            self.day, self.spent = today, 0.0

    def allow(self) -> bool:
        with self._lock:
            self._roll()
            return self.spent < self.cap

    def add(self, usd: float) -> None:
        with self._lock:
            self._roll()
            self.spent += usd


class RateLimit:
    """IP별 질문 수 상한: 최근 60초에 per_minute개, 하루(KST)에 per_day개.

    Render 같은 프록시 뒤라 X-Forwarded-For 첫 값을 클라이언트로 본다. 위조로 피할 수는 있지만
    비용의 최종선은 CostGuard의 하루 상한이다.
    """

    def __init__(self, per_minute: int, per_day: int,
                 clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(KST)):
        self.per_minute, self.per_day, self.clock = per_minute, per_day, clock
        self.recent: dict[str, deque] = {}
        self.day, self.daily = None, {}
        self._lock = threading.Lock()

    @staticmethod
    def client_ip(request: Request) -> str:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def allow(self, ip: str) -> bool:
        now = self.clock()
        with self._lock:
            if now.date() != self.day:
                self.day, self.daily = now.date(), {}
            q = self.recent.setdefault(ip, deque())
            while q and (now - q[0]).total_seconds() >= 60:
                q.popleft()
            if len(q) >= self.per_minute or self.daily.get(ip, 0) >= self.per_day:
                return False
            q.append(now)
            self.daily[ip] = self.daily.get(ip, 0) + 1
            return True


def _region_ko(code: str | None) -> str | None:
    return REGIONS[code][1] if code else None


def _call_view(call) -> dict[str, Any]:
    r = call.result
    view: dict[str, Any] = {"tool": call.name}
    if call.name == "run_sql":
        view.update(sql=call.input.get("sql", ""), query_id=r.get("query_id"), status=r.get("status"),
                    error=r.get("error") or None, columns=r.get("columns", []),
                    rows=r.get("rows", [])[:MAX_ROWS_SHOWN], row_count=r.get("row_count", 0),
                    truncated=bool(r.get("truncated")) or r.get("row_count", 0) > MAX_ROWS_SHOWN)
    elif call.name == "propose_refund":
        view.update(input=call.input, req_no=r.get("req_no"), status=r.get("status"), error=r.get("error"),
                    expires_at=r.get("expires_at"))
    elif call.name in ("search_docs", "read_doc"):
        keep = ("chunk_id", "doc_id", "version", "title", "status", "eff_from", "eff_to", "approval_no", "heading",
                "content")
        hits = r.get("results") if call.name == "search_docs" else [
            {**r.get("doc", {}), **c} for c in r.get("chunks", [])]
        view.update(input=call.input, error=r.get("error"),
                    chunks=[{k: h.get(k) for k in keep} for h in (hits or [])])
    else:
        view["input"] = call.input
    return view


def create_app(ask_fn: Callable[..., Any] | None = None, cost_cap_usd: float | None = None,
               rate_limit: RateLimit | None = None, audit_api: bool | None = None, dsn: str | None = None,
               user_password: str | None = None, approvals: bool | None = None) -> FastAPI:
    """ask_fn(db_user, question) → agent.Answer. 테스트에서는 가짜를 넣는다. 승인 화면은 dsn·user_password로 DB에 직접 붙는다."""
    if approvals is None:
        approvals = os.environ.get("ENABLE_APPROVALS") == "1"
    dsn = dsn or os.environ.get("DATABASE_URL")
    user_password = user_password or os.environ.get("TCN_USER_PASSWORD")
    cap = cost_cap_usd if cost_cap_usd is not None else float(os.environ.get("DAILY_COST_CAP_USD", "0.5"))
    guard = CostGuard(cap)
    limiter = rate_limit or RateLimit(int(os.environ.get("RATE_PER_MINUTE", "6")),
                                      int(os.environ.get("RATE_PER_DAY", "40")))
    if audit_api is None:
        audit_api = os.environ.get("ENABLE_AUDIT_API") == "1"

    if ask_fn is None:
        import anthropic
        dsn, pw = os.environ["DATABASE_URL"], os.environ["TCN_USER_PASSWORD"]
        key = (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("TREND_ANTHROPIC_API_KEY") or "").strip()
        if not key:  # 켜진 척하다 질문마다 500을 내지 않고, 배포 로그에 이유를 남기며 시작을 멈춘다
            raise RuntimeError("ANTHROPIC_API_KEY 환경변수가 없습니다 (이름·값을 확인하고 다시 배포하세요)")
        client = anthropic.Anthropic(api_key=key)
        model = os.environ.get("MODEL", "claude-haiku-5-5")
        audit_path = os.environ.get("AUDIT_PATH", "data/audit.jsonl")
        audit = JsonlAudit(audit_path)

        def ask_fn(db_user: str, question: str):
            return agent_mod.ask(client, Toolbox(dsn, pw, db_user, audit, max_rows=100, allow_propose=approvals),
                                 question, model=model)
    else:
        audit_path = None

    app = FastAPI(title="Tucanova 데이터 에이전트")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return resources.files("inhouse.web").joinpath("static/index.html").read_text(encoding="utf-8")

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        return {"approvals": approvals}

    @app.get("/api/personas")
    def personas() -> list[dict[str, Any]]:
        return [{"db_user": u, "role": p["role"], "region": _region_ko(p["region"])} for u, p in PERSONAS.items()]

    @app.post("/api/ask")
    def ask(body: Ask, request: Request) -> dict[str, Any]:
        if body.db_user not in PERSONAS:
            raise HTTPException(400, "알 수 없는 직원입니다. 목록에서 고르세요.")
        question = body.question.strip()
        if not question or len(question) > MAX_QUESTION:
            raise HTTPException(400, f"질문은 1~{MAX_QUESTION}자로 써 주세요.")
        if not guard.allow():
            raise HTTPException(429, "오늘의 데모 사용량을 다 썼습니다. 내일 다시 시도해 주세요.")
        if not limiter.allow(RateLimit.client_ip(request)):
            raise HTTPException(429, "질문이 너무 잦습니다. 잠시 뒤에 다시 시도해 주세요.")
        try:
            ans = ask_fn(body.db_user, question)
        except Exception as e:  # Claude API 오류(키·크레딧·과부하) 등. 원인 종류만 보여주고 자세한 건 서버 로그로
            import logging
            logging.getLogger("inhouse.web").exception("ask failed")
            name = type(e).__name__
            msg = ("Claude API 크레딧이 부족해 지금은 답할 수 없습니다 (운영자가 충전해야 합니다)."
                   if "credit balance" in str(e).lower()
                   else "Claude API 인증에 실패했습니다 (서버 설정 문제)." if "Authentication" in name or "Permission" in name
                   else "Claude API가 혼잡하거나 사용량 한도에 걸렸습니다. 잠시 뒤 다시 시도해 주세요." if name in
                   ("RateLimitError", "OverloadedError", "InternalServerError", "APIConnectionError", "APITimeoutError")
                   else f"답을 만드는 중 오류가 났습니다 ({name}).")
            raise HTTPException(502, msg)
        guard.add(ans.cost_usd)
        return {"db_user": body.db_user, "answer": ans.text, "cited": ans.cited,
                "doc_citations": getattr(ans, "doc_citations", []), "model": ans.model,
                "turns": ans.turns, "cost_usd": ans.cost_usd, "seconds": ans.seconds,
                "stop_reason": ans.stop_reason, "calls": [_call_view(c) for c in ans.calls]}

    # ---- 2단계 승인 화면 (ENABLE_APPROVALS) -------------------------------------------------
    def _need_approvals(db_user: str | None = None) -> None:
        if not approvals:
            raise HTTPException(404, "Not Found")
        if db_user is not None and db_user not in PERSONAS:
            raise HTTPException(400, "알 수 없는 직원입니다. 목록에서 고르세요.")

    @app.get("/approvals", response_class=HTMLResponse)
    def approvals_page() -> str:
        _need_approvals()
        return resources.files("inhouse.web").joinpath("static/approvals.html").read_text(encoding="utf-8")

    @app.get("/api/requests")
    def list_requests(db_user: str) -> dict[str, Any]:
        """그 직원 권한으로 보이는 환불 제안 (권역 매니저는 자기 권역 고객 주문만)."""
        _need_approvals(db_user)
        return {"db_user": db_user, "role": PERSONAS[db_user]["role"],
                "requests": refunds.list_requests(dsn, user_password, db_user)}

    @app.post("/api/requests/{req_no}/decide")
    def decide(req_no: int, body: Decide, request: Request) -> dict[str, Any]:
        """사람이 승인·거절한다. 규칙(승인권자·권역·한도·본인 제안·만료·이미 처리)은 DB 함수가 검사한다."""
        _need_approvals(body.db_user)
        if not limiter.allow(RateLimit.client_ip(request)):
            raise HTTPException(429, "요청이 너무 잦습니다. 잠시 뒤에 다시 시도해 주세요.")
        try:
            return refunds.decide(dsn, user_password, body.db_user, req_no, body.approve, body.note)
        except refunds.RefundError as e:
            raise HTTPException(403, str(e))

    @app.get("/api/audit")
    def audit_log(limit: int = 20) -> list[dict[str, Any]]:
        """최근 감사 기록 (질문·SQL·결과 상태). 데모라 직원 구분 없이 보여준다. 공개 배포에선 꺼 둔다."""
        if not audit_api:
            raise HTTPException(404, "Not Found")
        if not audit_path or not os.path.exists(audit_path):
            return []
        with open(audit_path, encoding="utf-8") as f:
            last = deque(f, maxlen=max(1, min(limit, 100)))
        return [json.loads(line) for line in reversed(last)]

    return app


def _lazy_app() -> FastAPI:
    return create_app()


# uvicorn inhouse.web.app:app — 환경변수가 있을 때만 만들어진다
app = _lazy_app() if os.environ.get("DATABASE_URL") and os.environ.get("TCN_USER_PASSWORD") else None
