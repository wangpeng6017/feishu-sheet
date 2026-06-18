"""即梦积分查询客户端（参考 iptag/jimeng-api 实现）。"""

from __future__ import annotations

import hashlib
import random
import time
import uuid
from dataclasses import dataclass
from typing import Any

import requests

BASE_URL_CN = "https://jimeng.jianying.com"
DEFAULT_ASSISTANT_ID_CN = 513695
REGION_CN = "cn"
PLATFORM_CODE = "7"
VERSION_CODE = "8.4.0"
WEB_VERSION = "7.5.0"
DA_VERSION = "3.3.9"

FAKE_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-language": "zh-CN,zh;q=0.9",
    "Cache-control": "no-cache",
    "Appvr": VERSION_CODE,
    "Pragma": "no-cache",
    "Priority": "u=1, i",
    "Pf": PLATFORM_CODE,
    "Sec-Ch-Ua": '"Google Chrome";v="142", "Chromium";v="142", "Not_A Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
    ),
}

WEB_ID = random.randint(7000000000000000000, 7999999999999999999)
USER_ID = uuid.uuid4().hex


@dataclass
class CreditInfo:
    gift_credit: int
    purchase_credit: int
    vip_credit: int

    @property
    def total_credit(self) -> int:
        return self.gift_credit + self.purchase_credit + self.vip_credit


def _unix_timestamp() -> int:
    return int(time.time())


def _generate_cookie(sessionid: str) -> str:
    ts = _unix_timestamp()
    return "; ".join(
        [
            f"_tea_web_id={WEB_ID}",
            "is_staff_user=false",
            f"sid_guard={sessionid}%7C{ts}%7C5184000%7CMon%2C+03-Feb-2025+08%3A17%3A09+GMT",
            f"uid_tt={USER_ID}",
            f"uid_tt_ss={USER_ID}",
            f"sid_tt={sessionid}",
            f"sessionid={sessionid}",
            f"sessionid_ss={sessionid}",
        ]
    )


def _make_sign(uri: str, device_time: int) -> str:
    raw = f"9e2c|{uri[-7:]}|{PLATFORM_CODE}|{VERSION_CODE}|{device_time}||11ac"
    return hashlib.md5(raw.encode()).hexdigest()


def _request(
    method: str,
    uri: str,
    sessionid: str = "",
    *,
    cookie: str | None = None,
    data: dict[str, Any] | None = None,
    extra_params: dict[str, Any] | None = None,
    referer: str = "https://jimeng.jianying.com/ai-tool/image/generate",
    no_default_params: bool = False,
) -> dict[str, Any]:
    device_time = _unix_timestamp()
    sign = _make_sign(uri, device_time)
    url = f"{BASE_URL_CN}{uri}"

    params: dict[str, Any] = {}
    if not no_default_params:
        params.update(
            {
                "aid": DEFAULT_ASSISTANT_ID_CN,
                "device_platform": "web",
                "region": REGION_CN,
                "webId": WEB_ID,
                "da_version": DA_VERSION,
                "os": "windows",
                "web_component_open_flag": 1,
                "web_version": WEB_VERSION,
                "aigc_features": "app_lip_sync",
            }
        )
    if extra_params:
        params.update(extra_params)

    cookie_header = cookie.strip() if cookie else _generate_cookie(sessionid)
    headers = {
        **FAKE_HEADERS,
        "Origin": BASE_URL_CN,
        "Referer": referer,
        "App-Sdk-Version": "48.0.0",
        "Appid": str(DEFAULT_ASSISTANT_ID_CN),
        "Cookie": cookie_header,
        "Device-Time": str(device_time),
        "Lan": "zh-Hans",
        "Loc": "cn",
        "Sign": sign,
        "Sign-Ver": "1",
        "Tdid": "",
        "Content-Type": "application/json",
    }

    response = requests.request(
        method=method,
        url=url,
        params=params,
        headers=headers,
        json=data or {},
        timeout=45,
    )
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"即梦 API 返回非 JSON (HTTP {response.status_code}): {response.text[:200]}"
        ) from exc

    ret = payload.get("ret")
    if ret not in (0, "0", None):
        errmsg = payload.get("errmsg") or payload.get("msg") or "未知错误"
        raise RuntimeError(f"即梦 API 错误 ret={ret}: {errmsg}")

    data_payload = payload.get("data", payload)
    if isinstance(data_payload, dict) and data_payload.get("error_code"):
        description = data_payload.get("description") or "未知错误"
        raise RuntimeError(f"即梦 API 错误: {description}")

    return data_payload


def get_credit(sessionid: str = "", cookie: str | None = None) -> CreditInfo:
    data = _request(
        "POST",
        "/commerce/v1/benefits/user_credit",
        sessionid,
        cookie=cookie,
        data={},
        no_default_params=True,
    )
    credit = data.get("credit") or {}
    return CreditInfo(
        gift_credit=int(credit.get("gift_credit") or 0),
        purchase_credit=int(credit.get("purchase_credit") or 0),
        vip_credit=int(credit.get("vip_credit") or 0),
    )


def receive_credit(sessionid: str = "", cookie: str | None = None) -> int:
    data = _request(
        "POST",
        "/commerce/v1/benefits/credit_receive",
        sessionid,
        cookie=cookie,
        data={"time_zone": "Asia/Shanghai"},
        referer="https://jimeng.jianying.com/ai-tool/home/",
    )
    return int(data.get("receive_quota") or 0)


def check_token_live(sessionid: str = "", cookie: str | None = None) -> bool:
    try:
        get_credit(sessionid=sessionid, cookie=cookie)
        return True
    except Exception:
        return False
