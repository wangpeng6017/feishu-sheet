#!/usr/bin/env python3
"""批量查询即梦账号积分并同步到飞书表格。"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from feishu_client import FeishuClient, col_letter
from jimeng_client import check_token_live, get_credit, receive_credit


def _resolve_jimeng_auth(account: dict[str, Any]) -> tuple[str, str | None]:
    sessionid = str(account.get("sessionid") or "").strip()
    cookie = str(account.get("cookie") or "").strip() or None
    if not cookie and not sessionid:
        raise ValueError("账号配置需要 cookie 或 sessionid")
    return sessionid, cookie


def _auth_error_message(cookie: str | None) -> str:
    if cookie:
        return "登录凭证无效，无法查询积分，请重新从浏览器 curl 复制 Cookie"
    return (
        "仅 sessionid 通常不够用，请从浏览器 Network 复制 curl 里的完整 Cookie（-b 后面的内容）"
    )


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"配置文件不存在: {path}\n请先复制 config.example.yaml 为 config.yaml 并填写配置"
        )
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list) and value:
        first = value[0]
        if isinstance(first, dict):
            return str(first.get("text") or first.get("name") or "")
        return str(first)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _resolve_feishu_target(feishu_cfg: dict[str, Any]) -> dict[str, str]:
    doc_type = feishu_cfg.get("type", "spreadsheet")
    if doc_type == "bitable":
        app_token = feishu_cfg.get("app_token", "")
        table_id = feishu_cfg.get("table_id", "")
        if not app_token or not table_id:
            raise ValueError("bitable 模式需要配置 app_token 和 table_id")
        return {"type": "bitable", "app_token": app_token, "table_id": table_id}

    spreadsheet_token = feishu_cfg.get("spreadsheet_token") or feishu_cfg.get(
        "app_token", ""
    )
    sheet_id = feishu_cfg.get("sheet_id", "")
    if not spreadsheet_token or not sheet_id:
        raise ValueError(
            "spreadsheet 模式需要配置 spreadsheet_token 和 sheet_id\n"
            "URL 示例: .../sheets/SPREADSHEET_TOKEN?sheet=SHEET_ID"
        )
    return {
        "type": "spreadsheet",
        "spreadsheet_token": spreadsheet_token,
        "sheet_id": sheet_id,
    }


def _build_header_index(header_row: list[Any], field_map: dict[str, str]) -> dict[str, int]:
    header_to_col = {_cell_text(name): idx for idx, name in enumerate(header_row)}
    index: dict[str, int] = {}
    for key, title in field_map.items():
        if title in header_to_col:
            index[key] = header_to_col[title]
    return index


def _build_sheet_row_index(
    rows: list[list[Any]],
    header_index: dict[str, int],
    match_by: str,
) -> dict[str, int]:
    key_name = "phone" if match_by == "phone" else "name"
    if key_name not in header_index:
        raise ValueError(f"表头中找不到匹配列: {key_name}")

    index: dict[str, int] = {}
    for row_number, row in enumerate(rows[1:], start=2):
        col = header_index[key_name]
        key = _cell_text(row[col] if col < len(row) else "")
        if key:
            index[key] = row_number
    return index


def _build_bitable_index(
    records: list[dict[str, Any]],
    field_map: dict[str, str],
    match_by: str,
) -> dict[str, dict[str, Any]]:
    key_field = field_map["phone" if match_by == "phone" else "name"]
    index: dict[str, dict[str, Any]] = {}
    for item in records:
        key = _cell_text(item.get("fields", {}).get(key_field))
        if key:
            index[key] = item
    return index


def _sheet_range(sheet_id: str, row: int, col: int) -> str:
    cell = f"{col_letter(col)}{row}"
    return f"{sheet_id}!{cell}:{cell}"


def sync_spreadsheet(
    feishu: FeishuClient,
    target: dict[str, str],
    field_map: dict[str, str],
    match_by: str,
    jimeng_cfg: dict[str, Any],
    dry_run: bool = False,
) -> int:
    spreadsheet_token = target["spreadsheet_token"]
    sheet_id = target["sheet_id"]

    rows = feishu.read_sheet_rows(spreadsheet_token, sheet_id)
    if not rows:
        raise RuntimeError("飞书表格为空，请至少保留一行表头")

    header_index = _build_header_index(rows[0], field_map)
    required = ["name", "current_credit"]
    missing = [field_map[k] for k in required if k not in header_index]
    if missing:
        raise ValueError(f"表头缺少必要列: {', '.join(missing)}")

    row_index = _build_sheet_row_index(rows, header_index, match_by)
    updates: list[tuple[str, list[list[Any]]]] = []
    append_rows: list[tuple[int, list[Any]]] = []
    success_count = 0
    next_row = max(row_index.values(), default=1) + 1

    for account in jimeng_cfg.get("accounts", []):
        channel = account.get("channel", "")
        name = account["name"]
        phone = str(account.get("phone") or "").strip()
        sessionid, cookie = _resolve_jimeng_auth(account)
        match_key = phone if match_by == "phone" else name

        print(f"\n处理: {channel} | {name} | {phone or '(无手机号)'}")

        try:
            if not check_token_live(sessionid, cookie=cookie):
                raise RuntimeError(_auth_error_message(cookie))

            if jimeng_cfg.get("auto_receive"):
                try:
                    receive_credit(sessionid, cookie=cookie)
                    print("  已尝试领取每日积分")
                except Exception as exc:
                    print(f"  领取积分失败（继续查询）: {exc}")

            credit = get_credit(sessionid, cookie=cookie)
            total = credit.total_credit
            print(
                f"  积分: 赠送={credit.gift_credit}, "
                f"购买={credit.purchase_credit}, VIP={credit.vip_credit}, "
                f"当前积分={total}"
            )
        except Exception as exc:
            print(f"  查询失败: {exc}")
            success_count += 1
            continue

        now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        existing_row = row_index.get(match_key)

        if existing_row:
            updates.append(
                (
                    _sheet_range(sheet_id, existing_row, header_index["current_credit"]),
                    [[total]],
                )
            )
            if "updated_at" in header_index:
                updates.append(
                    (
                        _sheet_range(
                            sheet_id, existing_row, header_index["updated_at"]
                        ),
                        [[now_text]],
                    )
                )
            action = f"更新第 {existing_row} 行"
        else:
            max_col = max(header_index.values())
            new_row = [""] * (max_col + 1)
            if "channel" in header_index:
                new_row[header_index["channel"]] = channel
            new_row[header_index["name"]] = name
            if phone and "phone" in header_index:
                new_row[header_index["phone"]] = phone
            new_row[header_index["current_credit"]] = total
            if "updated_at" in header_index:
                new_row[header_index["updated_at"]] = now_text
            append_rows.append((next_row, new_row))
            action = f"新增第 {next_row} 行"
            next_row += 1

        if dry_run:
            print(f"  [dry-run] {action}，当前积分={total}，更新时间={now_text}")
        else:
            print(f"  {action}，当前积分={total}，更新时间={now_text}")

        success_count += 1

    if dry_run:
        print(f"\n[dry-run] 将更新 {len(updates)} 个单元格，新增 {len(append_rows)} 行")
        return success_count

    feishu.batch_update_sheet_cells(spreadsheet_token, updates)

    append_updates: list[tuple[str, list[list[Any]]]] = []
    for row_number, row_values in append_rows:
        end_col = col_letter(len(row_values) - 1)
        append_updates.append(
            (f"{sheet_id}!A{row_number}:{end_col}{row_number}", [row_values])
        )
    feishu.batch_update_sheet_cells(spreadsheet_token, append_updates)

    return success_count


def sync_bitable(
    feishu: FeishuClient,
    target: dict[str, str],
    field_map: dict[str, str],
    match_by: str,
    jimeng_cfg: dict[str, Any],
    dry_run: bool = False,
) -> int:
    app_token = target["app_token"]
    table_id = target["table_id"]

    existing_records = feishu.list_all_records(app_token, table_id)
    record_index = _build_bitable_index(existing_records, field_map, match_by)

    to_create: list[dict[str, Any]] = []
    success_count = 0

    for account in jimeng_cfg.get("accounts", []):
        channel = account.get("channel", "")
        name = account["name"]
        phone = str(account.get("phone") or "").strip()
        sessionid, cookie = _resolve_jimeng_auth(account)
        match_key = phone if match_by == "phone" else name

        print(f"\n处理: {channel} | {name} | {phone or '(无手机号)'}")

        try:
            if not check_token_live(sessionid, cookie=cookie):
                raise RuntimeError(_auth_error_message(cookie))

            if jimeng_cfg.get("auto_receive"):
                try:
                    receive_credit(sessionid, cookie=cookie)
                    print("  已尝试领取每日积分")
                except Exception as exc:
                    print(f"  领取积分失败（继续查询）: {exc}")

            credit = get_credit(sessionid, cookie=cookie)
            total = credit.total_credit
            print(
                f"  积分: 赠送={credit.gift_credit}, "
                f"购买={credit.purchase_credit}, VIP={credit.vip_credit}, "
                f"当前积分={total}"
            )
        except Exception as exc:
            print(f"  查询失败: {exc}")
            success_count += 1
            continue

        now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        existing = record_index.get(match_key)

        if existing:
            fields = {field_map["current_credit"]: total}
        else:
            fields = {
                field_map["channel"]: channel,
                field_map["name"]: name,
                field_map["current_credit"]: total,
            }
            if phone:
                fields[field_map["phone"]] = phone

        if field_map.get("updated_at"):
            fields[field_map["updated_at"]] = now_text

        if dry_run:
            print(f"  [dry-run] 写入字段: {fields}")
            success_count += 1
            continue

        if existing:
            feishu.update_record(app_token, table_id, existing["record_id"], fields)
            print(f"  已更新「当前积分」: {total}，更新时间: {now_text}")
        else:
            to_create.append({"fields": fields})
            print(f"  待新增记录，当前积分: {total}，更新时间: {now_text}")

        success_count += 1

    if not dry_run and to_create:
        feishu.batch_create_records(app_token, table_id, to_create)
        print(f"\n批量新增 {len(to_create)} 条记录")

    return success_count


def sync_points(config_path: Path, dry_run: bool = False) -> int:
    config = load_config(config_path)

    feishu_cfg = config["feishu"]
    jimeng_cfg = config["jimeng"]
    field_map = feishu_cfg["fields"]
    match_by = feishu_cfg.get("match_by", "phone")

    target = _resolve_feishu_target(feishu_cfg)
    feishu = FeishuClient(feishu_cfg["app_id"], feishu_cfg["app_secret"])

    if target["type"] == "spreadsheet":
        success_count = sync_spreadsheet(
            feishu, target, field_map, match_by, jimeng_cfg, dry_run
        )
    else:
        success_count = sync_bitable(
            feishu, target, field_map, match_by, jimeng_cfg, dry_run
        )

    print(f"\n完成，共处理 {success_count} 个账号")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="同步即梦账号积分到飞书表格")
    parser.add_argument(
        "-c",
        "--config",
        default="config.yaml",
        help="配置文件路径，默认 config.yaml",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只查询积分并打印，不写入飞书",
    )
    args = parser.parse_args()

    try:
        return sync_points(Path(args.config), dry_run=args.dry_run)
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
