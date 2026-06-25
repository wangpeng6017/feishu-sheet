"""飞书 API 客户端，支持电子表格与多维表格两种模式。"""

from __future__ import annotations

import json
import time
from typing import Any

import requests

FEISHU_BASE = "https://open.feishu.cn/open-apis"


def col_letter(index: int) -> str:
    """0-based column index to Excel-style letter, e.g. 0 -> A, 3 -> D."""
    result = ""
    n = index + 1
    while n:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


class FeishuClient:
    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._tenant_access_token: str | None = None
        self._token_expire_at = 0.0

    def _get_tenant_access_token(self) -> str:
        now = time.time()
        if self._tenant_access_token and now < self._token_expire_at - 60:
            return self._tenant_access_token

        resp = requests.post(
            f"{FEISHU_BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取 tenant_access_token 失败: {data}")

        self._tenant_access_token = data["tenant_access_token"]
        self._token_expire_at = now + int(data.get("expire", 7200))
        return self._tenant_access_token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_tenant_access_token()}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        resp = requests.request(
            method,
            f"{FEISHU_BASE}{path}",
            headers=self._headers(),
            timeout=30,
            **kwargs,
        )
        try:
            data = resp.json()
        except ValueError:
            resp.raise_for_status()
            raise RuntimeError(f"飞书 API 返回非 JSON: HTTP {resp.status_code}")

        if resp.status_code >= 400 or data.get("code") not in (0, None):
            if data.get("code") == 0:
                resp.raise_for_status()
            raise RuntimeError(
                f"飞书 API 错误 (HTTP {resp.status_code}): "
                f"{json.dumps(data, ensure_ascii=False)}"
            )
        return data

    # --- 电子表格 ---

    def read_sheet_rows(
        self,
        spreadsheet_token: str,
        sheet_id: str,
        max_rows: int = 500,
        max_cols: int = 26,
    ) -> list[list[Any]]:
        end_col = col_letter(max_cols - 1)
        range_str = f"{sheet_id}!A1:{end_col}{max_rows}"
        data = self._request(
            "GET",
            f"/sheets/v2/spreadsheets/{spreadsheet_token}/values/{range_str}",
        )
        return data.get("data", {}).get("valueRange", {}).get("values") or []

    def batch_update_sheet_cells(
        self,
        spreadsheet_token: str,
        updates: list[tuple[str, list[list[Any]]]],
    ) -> None:
        if not updates:
            return

        value_ranges = [
            {"range": range_str, "values": values}
            for range_str, values in updates
        ]
        self._request(
            "POST",
            f"/sheets/v2/spreadsheets/{spreadsheet_token}/values_batch_update",
            json={"valueRanges": value_ranges},
        )

    def batch_update_sheet_styles(
        self,
        spreadsheet_token: str,
        styles: list[tuple[str, dict[str, Any]]],
    ) -> None:
        if not styles:
            return

        self._request(
            "PUT",
            f"/sheets/v2/spreadsheets/{spreadsheet_token}/styles_batch_update",
            json={
                "data": [
                    {"ranges": [range_str], "style": style}
                    for range_str, style in styles
                ]
            },
        )

    def append_sheet_row(
        self,
        spreadsheet_token: str,
        sheet_id: str,
        row_number: int,
        values: list[Any],
        max_cols: int = 26,
    ) -> None:
        end_col = col_letter(len(values) - 1 if values else max_cols - 1)
        range_str = f"{sheet_id}!A{row_number}:{end_col}{row_number}"
        self.batch_update_sheet_cells(
            spreadsheet_token,
            [(range_str, [values])],
        )

    # --- 多维表格 ---

    def list_all_records(self, app_token: str, table_id: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        page_token: str | None = None

        while True:
            params: dict[str, Any] = {"page_size": 500}
            if page_token:
                params["page_token"] = page_token

            data = self._request(
                "GET",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
                params=params,
            )
            items = data.get("data", {}).get("items") or []
            records.extend(items)

            page_token = data.get("data", {}).get("page_token")
            if not page_token:
                break

        return records

    def update_record(
        self,
        app_token: str,
        table_id: str,
        record_id: str,
        fields: dict[str, Any],
    ) -> None:
        self._request(
            "PUT",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
            json={"fields": fields},
        )

    def batch_create_records(
        self, app_token: str, table_id: str, records: list[dict[str, Any]]
    ) -> None:
        if not records:
            return

        for i in range(0, len(records), 500):
            chunk = records[i : i + 500]
            self._request(
                "POST",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
                json={"records": chunk},
            )
