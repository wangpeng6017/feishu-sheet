"""LibTV（liblib.tv）算力查询客户端。"""

from __future__ import annotations

from typing import Any

import requests

API_BASE = "https://api.liblib.art"
MEMBER_ACCOUNT_URI = "/api/www/member/account"

FAKE_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Origin": "https://www.liblib.tv",
    "Referer": "https://www.liblib.tv/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
}


def _request(token: str) -> dict[str, Any]:
    if not token:
        raise RuntimeError("LibTV 缺少 libtv_token，请从 liblib.tv 网页复制 usertoken_online")

    response = requests.get(
        f"{API_BASE}{MEMBER_ACCOUNT_URI}",
        headers={**FAKE_HEADERS, "token": token},
        timeout=45,
    )
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"LibTV API 返回非 JSON (HTTP {response.status_code}): "
            f"{response.text[:200]}"
        ) from exc

    code = payload.get("code")
    if code not in (0, "0", None):
        msg = payload.get("msg") or payload.get("message") or "未知错误"
        raise RuntimeError(f"LibTV API 错误 code={code}: {msg}")

    data = payload.get("data")
    if data is None:
        raise RuntimeError("LibTV API 返回空数据")
    return data if isinstance(data, dict) else {"items": data}


def _attr_power(attr: dict[str, Any]) -> int | None:
    """解析账号可用算力（与网页展示一致，含会员算力 + 模型卡算力）。"""
    total = 0
    found = False

    usable = attr.get("usablePower")
    if isinstance(usable, (int, float)):
        total += max(0, int(usable))
        found = True

    ex_summary = attr.get("exPowerSummary")
    if isinstance(ex_summary, dict):
        ex_usable = ex_summary.get("usablePower")
        if isinstance(ex_usable, (int, float)):
            total += max(0, int(ex_usable))
            found = True

    if found:
        return total

    libtv_usable = attr.get("libtvUsablePower")
    if isinstance(libtv_usable, (int, float)):
        return max(0, int(libtv_usable))

    member_total = attr.get("totalPower")
    used = attr.get("usedPower")
    if isinstance(member_total, (int, float)):
        used_val = int(used) if isinstance(used, (int, float)) else 0
        return max(0, int(member_total) - used_val)

    return None


def _item_power(item: dict[str, Any]) -> int | None:
    attr = item.get("attr")
    if isinstance(attr, dict):
        power = _attr_power(attr)
        if power is not None:
            return power

    member_account = item.get("memberAccount")
    if isinstance(member_account, dict):
        attr = member_account.get("attr")
        if isinstance(attr, dict):
            power = _attr_power(attr)
            if power is not None:
                return power

    for key in ("libtvUsablePower", "usablePower", "remainPower", "power"):
        value = item.get(key)
        if isinstance(value, (int, float)):
            return max(0, int(value))

    return None


def _parse_power(data: dict[str, Any]) -> int:
    items = data.get("items")
    if isinstance(items, list):
        powers = [p for p in (_item_power(item) for item in items) if p is not None]
        if powers:
            return sum(powers)

    power = _item_power(data)
    if power is not None:
        return power

    raise RuntimeError("LibTV API 响应中未找到算力字段")


def get_credit(token: str) -> int:
    data = _request(token.strip())
    return _parse_power(data)


def check_token_live(token: str) -> bool:
    try:
        get_credit(token)
        return True
    except Exception:
        return False
