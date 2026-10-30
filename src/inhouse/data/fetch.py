"""Olist 공개 데이터를 내려받아 해시를 확인하고 푼다.

데이터 라이선스: CC BY-NC-SA 4.0 (Olist, Kaggle olistbr/brazilian-ecommerce).
라이선스 조건(가공 데이터 재배포 시 같은 라이선스) 때문에 원본·가공 CSV는 레포에 넣지 않는다.
사용법: python -m inhouse.data.fetch [--dir data/raw]
"""
from __future__ import annotations

import argparse
import hashlib
import os
import urllib.request
import zipfile

URL = "https://www.kaggle.com/api/v1/datasets/download/olistbr/brazilian-ecommerce"
SHA256 = "967e41e04fc306fe604e2a693f488995a8b41e5047418f8a5c8e4abd6deca784"  # 2026-10-30에 받은 zip


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(directory: str) -> None:
    os.makedirs(directory, exist_ok=True)
    zpath = os.path.join(directory, "olist.zip")
    if not os.path.exists(zpath):
        print(f"내려받는 중: {URL}")
        urllib.request.urlretrieve(URL, zpath)
    got = sha256(zpath)
    if got != SHA256:
        raise SystemExit(f"해시가 다릅니다 (데이터셋이 바뀌었을 수 있음): {got}")
    with zipfile.ZipFile(zpath) as z:
        z.extractall(directory)
    print(f"완료: {directory}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="data/raw")
    fetch(ap.parse_args().dir)
