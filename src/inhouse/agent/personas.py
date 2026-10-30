"""직원 DB 역할 → 사람이 읽는 역할 설명 (시스템 프롬프트용). overlay._employees와 같은 규칙."""
from __future__ import annotations

from ..data.transform import REGIONS

PERSONAS: dict[str, dict[str, str | None]] = {"u_e001": {"role": "본사 분석가", "region": None}}
for i, (cd, (_, ko, _ufs)) in enumerate(REGIONS.items(), 1):
    PERSONAS[f"u_e1{i:02d}"] = {"role": f"{ko} 권역 매니저", "region": cd}
PERSONAS.update({"u_e201": {"role": "CS 담당", "region": None}, "u_e202": {"role": "CS 담당", "region": None}})


def describe(db_user: str) -> str:
    p = PERSONAS.get(db_user)
    if p is None:
        return "알 수 없는 사용자"
    scope = "자기 권역 데이터만 보입니다" if p["region"] else "전국 데이터가 보입니다"
    pii = "고객 연락처는 마스킹 뷰(v_cust_contact)로만 볼 수 있습니다" if p["role"] == "CS 담당" else "고객 개인정보는 볼 수 없습니다"
    return f"{p['role']} ({db_user}). {scope}. {pii}."
