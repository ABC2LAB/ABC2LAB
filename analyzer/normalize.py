"""결정적 canonical ID (명세 9.4). 같은 입력이면 항상 같은 ID."""
from __future__ import annotations

import re


def api_id(method: str, endpoint: str) -> str:
    # api:{METHOD}:{endpoint}  — method 대문자, endpoint 는 크롤러 정규화값 그대로
    return f"api:{method.upper()}:{endpoint}"


def param_id(api_id_: str, location: str, parameter_path: str) -> str:
    # param:{api_id}:{location}:{parameter_path}
    return f"{'param:' + api_id_}:{location}:{parameter_path}"


def resource_id(canonical_name: str) -> str:
    # resource:{canonical_name}  — lowercase kebab-case
    slug = re.sub(r"[^a-z0-9]+", "-", canonical_name.strip().lower()).strip("-")
    return f"resource:{slug or 'resource'}"
