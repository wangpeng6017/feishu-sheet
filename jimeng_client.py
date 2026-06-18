"""即梦 / 小云雀积分查询客户端。"""

from __future__ import annotations

import hashlib
import random
import time
import uuid
from dataclasses import dataclass
from typing import Any

import requests

PLATFORM_CODE = "7"

FAKE_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-language": "zh-CN,zh;q=0.9",
    "Cache-control": "no-cache",
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
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
}

WEB_ID = random.randint(7000000000000000000, 7999999999999999999)
USER_ID = uuid.uuid4().hex


@dataclass(frozen=True)
class PlatformConfig:
    base_url: str
    app_id: str
    version_code: str
    referer: str
    receive_body: dict[str, Any]
    sign_prefix: str = "9e2c"
    use_entrance_from: bool = False


PLATFORMS: dict[str, PlatformConfig] = {
    "jimeng": PlatformConfig(
        base_url="https://jimeng.jianying.com",
        app_id="513695",
        version_code="8.4.0",
        referer="https://jimeng.jianying.com/ai-tool/image/generate",
        receive_body={"time_zone": "Asia/Shanghai"},
    ),
    "xyq": PlatformConfig(
        base_url="https://xyq.jianying.com",
        app_id="795647",
        version_code="5.8.0",
        referer="https://xyq.jianying.com/",
        receive_body={"source": "web"},
        use_entrance_from=True,
    ),
}


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


def _make_sign(uri: str, device_time: int, platform: PlatformConfig) -> str:
    raw = (
        f"{platform.sign_prefix}|{uri[-7:]}|{PLATFORM_CODE}|"
        f"{platform.version_code}|{device_time}||11ac"
    )
    return hashlib.md5(raw.encode()).hexdigest()


def _get_platform(platform: str) -> PlatformConfig:
    cfg = PLATFORMS.get(platform)
    if not cfg:
        raise ValueError(f"不支持的平台: {platform}，可选: {', '.join(PLATFORMS)}")
    return cfg


def _request(
    method: str,
    uri: str,
    platform: str,
    sessionid: str = "",
    *,
    cookie: str | None = None,
    data: dict[str, Any] | None = None,
    extra_params: dict[str, Any] | None = None,
    referer: str | None = None,
    no_default_params: bool = False,
) -> dict[str, Any]:
    cfg = _get_platform(platform)
    device_time = _unix_timestamp()
    sign = _make_sign(uri, device_time, cfg)
    url = f"{cfg.base_url}{uri}"

    params: dict[str, Any] = {}
    if not no_default_params and platform == "jimeng":
        params.update(
            {
                "aid": int(cfg.app_id),
                "device_platform": "web",
                "region": "cn",
                "webId": WEB_ID,
                "da_version": "3.3.9",
                "os": "windows",
                "web_component_open_flag": 1,
                "web_version": "7.5.0",
                "aigc_features": "app_lip_sync",
            }
        )
    if extra_params:
        params.update(extra_params)

    cookie_header = cookie.strip() if cookie else _generate_cookie(sessionid)
    headers = {
        **FAKE_HEADERS,
        "Origin": cfg.base_url,
        "Referer": referer or cfg.referer,
        "Appvr": cfg.version_code,
        "Appid": cfg.app_id,
        "Cookie": cookie_header,
        "Device-Time": str(device_time),
        "Sign": sign,
        "Sign-Ver": "1",
        "Tdid": "",
        "Content-Type": "application/json",
    }
    if cfg.use_entrance_from:
        headers["entrance-from"] = "web"
        headers["Loc"] = "CN"
    else:
        headers["App-Sdk-Version"] = "48.0.0"
        headers["Lan"] = "zh-Hans"
        headers["Loc"] = "cn"

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
            f"{platform} API 返回非 JSON (HTTP {response.status_code}): "
            f"{response.text[:200]}"
        ) from exc

    ret = payload.get("ret")
    if ret not in (0, "0", None):
        errmsg = payload.get("errmsg") or payload.get("msg") or "未知错误"
        raise RuntimeError(f"{platform} API 错误 ret={ret}: {errmsg}")

    data_payload = payload.get("data", payload)
    if isinstance(data_payload, dict) and data_payload.get("error_code"):
        description = data_payload.get("description") or "未知错误"
        raise RuntimeError(f"{platform} API 错误: {description}")

    return data_payload


def get_credit(
    sessionid: str = "",
    cookie: str | None = None,
    *,
    platform: str = "jimeng",
) -> CreditInfo:
    data = _request(
        "POST",
        "/commerce/v1/benefits/user_credit",
        platform,
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


def receive_credit(
    sessionid: str = "",
    cookie: str | None = None,
    *,
    platform: str = "jimeng",
) -> int:
    cfg = _get_platform(platform)
    receive_referer = (
        f"{cfg.base_url}/"
        if platform == "xyq"
        else "https://jimeng.jianying.com/ai-tool/home/"
    )
    data = _request(
        "POST",
        "/commerce/v1/benefits/credit_receive",
        platform,
        sessionid,
        cookie=cookie,
        data=cfg.receive_body,
        referer=receive_referer,
    )
    return int(data.get("receive_quota") or 0)


def check_token_live(
    sessionid: str = "",
    cookie: str | None = None,
    *,
    platform: str = "jimeng",
) -> bool:
    try:
        get_credit(sessionid=sessionid, cookie=cookie, platform=platform)
        return True
    except Exception:
        return False
