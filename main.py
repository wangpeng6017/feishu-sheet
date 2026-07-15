#!/usr/bin/env python3
"""批量查询即梦 / 小云雀 / LibTV 积分并同步到飞书表格。"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from feishu_client import FeishuClient, col_letter
from jimeng_client import check_token_live, get_credit, receive_credit
import libtv_client

PLATFORM_LABELS = {
    "jimeng": "即梦",
    "xyq": "小云雀",
    "libtv": "LibTV",
}

TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")
REFRESH_WARN_STYLE = {
    "backColor": "#FFC7CE",
    "foreColor": "#9C0006",
    "font": {"bold": True},
}
REFRESH_NORMAL_STYLE = {
    "backColor": "#FFFFFF",
    "foreColor": "#000000",
    "font": {"bold": False},
}
AUTH_FAIL_MARK = "请重新提供Cookie/Token"
_STATUS_MARK_RE = re.compile(
    r"^(?:请重新提供Cookie/Token|【(?:登录态失效|查询失败)】[^；;]*)[；;]?\s*"
)


def _today() -> date:
    return datetime.now(TZ_SHANGHAI).date()


def _now_text() -> str:
    return datetime.now(TZ_SHANGHAI).strftime("%Y-%m-%d %H:%M:%S")


def _parse_refresh_anchor(value: Any) -> date | None:
    """解析「账号积分刷新时间」，取其中的日作为每月刷新日。"""
    if value is None or value == "":
        return None

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = _cell_text(value).replace("/", ".").replace("-", ".")
    if not text:
        return None

    match = re.fullmatch(r"(\d{4})\.(\d{1,2})\.(\d{1,2})", text)
    if match:
        year, month, day = (int(match.group(i)) for i in range(1, 4))
        try:
            return date(year, month, day)
        except ValueError:
            return None

    for fmt in ("%Y.%m.%d", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    if isinstance(value, (int, float)):
        base = date(1899, 12, 30)
        try:
            return base + timedelta(days=int(value))
        except OverflowError:
            return None

    return None


def _next_refresh_date(anchor: date, today: date | None = None) -> date:
    """根据刷新锚定日期的「日」，计算下一次刷新日期。"""
    import calendar

    today = today or _today()
    refresh_day = anchor.day

    def clamp_day(year: int, month: int) -> int:
        return min(refresh_day, calendar.monthrange(year, month)[1])

    candidate = date(today.year, today.month, clamp_day(today.year, today.month))
    if candidate >= today:
        return candidate

    if today.month == 12:
        year, month = today.year + 1, 1
    else:
        year, month = today.year, today.month + 1
    return date(year, month, clamp_day(year, month))


def _days_until_refresh(anchor: date, today: date | None = None) -> int:
    today = today or _today()
    return (_next_refresh_date(anchor, today) - today).days


def _resolve_account_cookies(account: dict[str, Any]) -> dict[str, str | None]:
    jimeng_cookie = str(account.get("cookie") or "").strip() or None
    xyq_cookie = str(account.get("xyq_cookie") or "").strip() or None
    libtv_token = str(account.get("libtv_token") or "").strip() or None
    sessionid = str(account.get("sessionid") or "").strip()

    if (
        not jimeng_cookie
        and not xyq_cookie
        and not libtv_token
        and not sessionid
    ):
        raise ValueError(
            "账号至少需配置一项：cookie（即梦）、xyq_cookie（小云雀）、"
            "libtv_token（LibTV）或 sessionid（即梦旧方式）"
        )

    return {
        "jimeng": jimeng_cookie,
        "xyq": xyq_cookie,
        "libtv_token": libtv_token,
        "sessionid": sessionid or "",
    }


def _auth_error_message(platform: str) -> str:
    label = PLATFORM_LABELS.get(platform, platform)
    if platform == "libtv":
        return (
            f"{label} 登录凭证无效，请从 liblib.tv 网页复制 usertoken_online 填到 libtv_token"
        )
    return (
        f"{label} 登录凭证无效，请从 xyq.jianying.com 或 jimeng.jianying.com 复制 curl Cookie"
    )


def _fetch_account_credits(
    account: dict[str, Any], jimeng_cfg: dict[str, Any]
) -> dict[str, int]:
    cookies = _resolve_account_cookies(account)
    sessionid = cookies["sessionid"]
    auto_receive = jimeng_cfg.get("auto_receive", False)
    credits: dict[str, int] = {}
    errors: list[str] = []

    for platform in ("jimeng", "xyq", "libtv"):
        label = PLATFORM_LABELS[platform]

        if platform == "libtv":
            libtv_token = cookies["libtv_token"]
            if not libtv_token:
                print(f"  [{label}] 未配置 libtv_token，跳过")
                continue
            try:
                if not libtv_client.check_token_live(libtv_token):
                    raise RuntimeError(_auth_error_message(platform))
                total = libtv_client.get_credit(libtv_token)
                credits[platform] = total
                print(f"  [{label}] 算力: {total}")
            except Exception as exc:
                errors.append(f"{label}: {exc}")
                print(f"  [{label}] 查询失败: {exc}")
            continue

        cookie = cookies[platform]
        if platform == "xyq" and not cookie:
            print(f"  [{label}] 未配置 xyq_cookie，跳过")
            continue
        if platform == "jimeng" and not cookie and not sessionid:
            print(f"  [{label}] 未配置 cookie，跳过")
            continue

        try:
            if not check_token_live(sessionid, cookie=cookie, platform=platform):
                raise RuntimeError(_auth_error_message(platform))

            if auto_receive:
                try:
                    receive_credit(sessionid, cookie=cookie, platform=platform)
                    print(f"  [{label}] 已尝试领取每日积分")
                except Exception as exc:
                    print(f"  [{label}] 领取积分失败（继续查询）: {exc}")

            credit = get_credit(sessionid, cookie=cookie, platform=platform)
            total = credit.total_credit
            credits[platform] = total
            print(
                f"  [{label}] 积分: 赠送={credit.gift_credit}, "
                f"购买={credit.purchase_credit}, VIP={credit.vip_credit}, "
                f"总计={total}"
            )
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            print(f"  [{label}] 查询失败: {exc}")

    if not credits and errors:
        raise RuntimeError("; ".join(errors))
    return credits


def _combined_credit(credit_totals: dict[str, int]) -> int:
    """即梦 + 小云雀 + LibTV 积分合计（未配置的平台不计入）。"""
    return sum(credit_totals.values())


def _is_auth_error(exc: BaseException) -> bool:
    return "登录凭证无效" in str(exc)


def _fail_remark_text(exc: BaseException) -> str:
    if _is_auth_error(exc):
        return AUTH_FAIL_MARK
    brief = str(exc).replace("\n", " ").strip()
    if len(brief) > 60:
        brief = brief[:57] + "..."
    return f"【查询失败】{brief}"


def _with_status_remark(existing: str, mark: str) -> str:
    base = _STATUS_MARK_RE.sub("", (existing or "").strip()).strip("；; ").strip()
    return f"{mark}；{base}" if base else mark


def _clear_status_remark(existing: str) -> str:
    return _STATUS_MARK_RE.sub("", (existing or "").strip()).strip("；; ").strip()


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


_FIELD_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "current_credit": ("当前积分", "当前即梦积分"),
}


def _resolve_field_header(field_map: dict[str, str], key: str) -> str:
    return field_map.get(key, "")


def _field_header_titles(field_map: dict[str, str], key: str) -> tuple[str, ...]:
    primary = _resolve_field_header(field_map, key)
    aliases = _FIELD_HEADER_ALIASES.get(key, ())
    titles: list[str] = []
    for title in (primary, *aliases):
        if title and title not in titles:
            titles.append(title)
    return tuple(titles)


def _build_header_index(header_row: list[Any], field_map: dict[str, str]) -> dict[str, int]:
    header_to_col = {_cell_text(name): idx for idx, name in enumerate(header_row)}
    index: dict[str, int] = {}
    for key in field_map:
        for title in _field_header_titles(field_map, key):
            if title in header_to_col:
                index[key] = header_to_col[title]
                break
    return index


_SHEET_FIELD_ORDER = (
    "channel",
    "name",
    "phone",
    "current_credit",
    "updated_at",
    "credit_refresh_at",
    "days_until_refresh",
    "remark",
)


def _ensure_sheet_headers(
    feishu: FeishuClient,
    spreadsheet_token: str,
    sheet_id: str,
    header_row: list[Any],
    field_map: dict[str, str],
    *,
    dry_run: bool = False,
) -> list[Any]:
    """补全飞书表头缺失列，避免查到积分却写不进去。"""
    header_to_col = {
        _cell_text(name): idx for idx, name in enumerate(header_row) if _cell_text(name)
    }
    row = list(header_row)
    updates: list[tuple[str, list[list[Any]]]] = []

    for key in _SHEET_FIELD_ORDER:
        titles = _field_header_titles(field_map, key)
        if not titles or any(title in header_to_col for title in titles):
            continue
        title = titles[0]

        insert_at = 0
        for prev in _SHEET_FIELD_ORDER:
            if prev == key:
                break
            for prev_title in _field_header_titles(field_map, prev):
                if prev_title in header_to_col:
                    insert_at = header_to_col[prev_title] + 1
                    break

        if insert_at < len(row) and _cell_text(row[insert_at]):
            insert_at = max(header_to_col.values(), default=-1) + 1

        while len(row) <= insert_at:
            row.append(None)

        row[insert_at] = title
        header_to_col[title] = insert_at
        cell = f"{col_letter(insert_at)}1"
        updates.append((f"{sheet_id}!{cell}:{cell}", [[title]]))
        print(f"  飞书表头缺少「{title}」，将写入第 {insert_at + 1} 列（{cell}）")

    if updates and not dry_run:
        feishu.batch_update_sheet_cells(spreadsheet_token, updates)

    return row


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


def _sync_refresh_reminders(
    feishu: FeishuClient,
    spreadsheet_token: str,
    sheet_id: str,
    rows: list[list[Any]],
    header_index: dict[str, int],
    field_map: dict[str, str],
    *,
    warning_days: int = 7,
    dry_run: bool = False,
) -> None:
    """根据「账号积分刷新时间」计算距下次刷新天数，7 天内标红提醒。"""
    refresh_col = header_index.get("credit_refresh_at")
    days_col = header_index.get("days_until_refresh")
    if refresh_col is None:
        title = field_map.get("credit_refresh_at", "账号积分刷新时间")
        print(f"\n跳过积分刷新提醒：表头缺少「{title}」列")
        return
    if days_col is None:
        title = field_map.get("days_until_refresh", "距下次刷新(天)")
        print(f"\n跳过积分刷新提醒：表头缺少「{title}」列")
        return

    days_title = field_map.get("days_until_refresh", "距下次刷新(天)")
    today = _today()
    updates: list[tuple[str, list[list[Any]]]] = []
    styles: list[tuple[str, dict[str, Any]]] = []
    warn_count = 0
    filled_count = 0

    for row_number, row in enumerate(rows[1:], start=2):
        raw = row[refresh_col] if refresh_col < len(row) else None
        anchor = _parse_refresh_anchor(raw)
        if anchor is None:
            updates.append(
                (_sheet_range(sheet_id, row_number, days_col), [[""]])
            )
            styles.append(
                (_sheet_range(sheet_id, row_number, days_col), REFRESH_NORMAL_STYLE)
            )
            continue

        days = _days_until_refresh(anchor, today)
        updates.append(
            (_sheet_range(sheet_id, row_number, days_col), [[days]])
        )
        style = REFRESH_WARN_STYLE if days < warning_days else REFRESH_NORMAL_STYLE
        styles.append((_sheet_range(sheet_id, row_number, days_col), style))
        filled_count += 1
        if days < warning_days:
            warn_count += 1

    if dry_run:
        print(
            f"\n[dry-run] 将更新 {filled_count} 行「{days_title}」，"
            f"其中 {warn_count} 行距刷新不足 {warning_days} 天（标红）"
        )
        return

    if updates:
        feishu.batch_update_sheet_cells(spreadsheet_token, updates)
    if styles:
        feishu.batch_update_sheet_styles(spreadsheet_token, styles)

    print(
        f"\n已更新 {filled_count} 行「{days_title}」，"
        f"其中 {warn_count} 行距刷新不足 {warning_days} 天（已标红）"
    )


def sync_spreadsheet(
    feishu: FeishuClient,
    target: dict[str, str],
    field_map: dict[str, str],
    match_by: str,
    jimeng_cfg: dict[str, Any],
    dry_run: bool = False,
    refresh_warning_days: int = 7,
) -> int:
    spreadsheet_token = target["spreadsheet_token"]
    sheet_id = target["sheet_id"]

    rows = feishu.read_sheet_rows(spreadsheet_token, sheet_id)
    if not rows:
        raise RuntimeError("飞书表格为空，请至少保留一行表头")

    rows[0] = _ensure_sheet_headers(
        feishu, spreadsheet_token, sheet_id, rows[0], field_map, dry_run=dry_run
    )
    header_index = _build_header_index(rows[0], field_map)
    if "name" not in header_index:
        raise ValueError(f"表头缺少必要列: {field_map.get('name', '名称')}")
    if "current_credit" not in header_index:
        raise ValueError(f"表头缺少必要列: {field_map.get('current_credit', '当前积分')}")

    row_index = _build_sheet_row_index(rows, header_index, match_by)
    updates: list[tuple[str, list[list[Any]]]] = []
    styles: list[tuple[str, dict[str, Any]]] = []
    append_rows: list[tuple[int, list[Any]]] = []
    success_count = 0
    next_row = max(row_index.values(), default=1) + 1

    def _existing_remark(row_number: int) -> str:
        if "remark" not in header_index:
            return ""
        col = header_index["remark"]
        row = rows[row_number - 1] if 0 < row_number <= len(rows) else []
        return _cell_text(row[col] if col < len(row) else "")

    def _write_remark(row_number: int, text: str, *, warn: bool) -> None:
        if "remark" not in header_index:
            return
        col = header_index["remark"]
        updates.append((_sheet_range(sheet_id, row_number, col), [[text]]))
        styles.append(
            (
                _sheet_range(sheet_id, row_number, col),
                REFRESH_WARN_STYLE if warn else REFRESH_NORMAL_STYLE,
            )
        )

    for account in jimeng_cfg.get("accounts", []):
        channel = account.get("channel", "")
        name = account["name"]
        phone = str(account.get("phone") or "").strip()
        match_key = phone if match_by == "phone" else name

        print(f"\n处理: {channel} | {name} | {phone or '(无手机号)'}")

        try:
            credit_totals = _fetch_account_credits(account, jimeng_cfg)
        except Exception as exc:
            mark = _fail_remark_text(exc)
            existing_row = row_index.get(match_key)
            print(f"  查询失败: {exc}")
            if existing_row:
                remark_text = _with_status_remark(_existing_remark(existing_row), mark)
                _write_remark(existing_row, remark_text, warn=True)
                if dry_run:
                    print(f"  [dry-run] 第 {existing_row} 行备注标记为: {remark_text}")
                else:
                    print(f"  已标记第 {existing_row} 行: {remark_text}")
            else:
                print("  表格中无对应行，跳过标记")
            success_count += 1
            continue

        now_text = _now_text()
        existing_row = row_index.get(match_key)
        total_credit = _combined_credit(credit_totals)

        if existing_row:
            if "current_credit" in header_index:
                updates.append(
                    (
                        _sheet_range(
                            sheet_id, existing_row, header_index["current_credit"]
                        ),
                        [[total_credit]],
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
            if "remark" in header_index:
                cleared = _clear_status_remark(_existing_remark(existing_row))
                _write_remark(existing_row, cleared, warn=False)
            action = f"更新第 {existing_row} 行"
        else:
            max_col = max(header_index.values())
            new_row = [""] * (max_col + 1)
            if "channel" in header_index:
                new_row[header_index["channel"]] = channel
            new_row[header_index["name"]] = name
            if phone and "phone" in header_index:
                new_row[header_index["phone"]] = phone
            if "current_credit" in header_index:
                new_row[header_index["current_credit"]] = total_credit
            if "updated_at" in header_index:
                new_row[header_index["updated_at"]] = now_text
            append_rows.append((next_row, new_row))
            action = f"新增第 {next_row} 行"
            next_row += 1

        summary = ", ".join(
            f"{PLATFORM_LABELS[k]}={v}" for k, v in credit_totals.items()
        )
        credit_text = f"当前积分={total_credit}（{summary}）" if len(credit_totals) > 1 else f"当前积分={total_credit}"
        if dry_run:
            print(f"  [dry-run] {action}，{credit_text}，更新时间={now_text}")
        else:
            print(f"  {action}，{credit_text}，更新时间={now_text}")

        success_count += 1

    if dry_run:
        print(f"\n[dry-run] 将更新 {len(updates)} 个单元格，新增 {len(append_rows)} 行")
    else:
        feishu.batch_update_sheet_cells(spreadsheet_token, updates)
        if styles:
            feishu.batch_update_sheet_styles(spreadsheet_token, styles)

        append_updates: list[tuple[str, list[list[Any]]]] = []
        for row_number, row_values in append_rows:
            end_col = col_letter(len(row_values) - 1)
            append_updates.append(
                (f"{sheet_id}!A{row_number}:{end_col}{row_number}", [row_values])
            )
        feishu.batch_update_sheet_cells(spreadsheet_token, append_updates)

    _sync_refresh_reminders(
        feishu,
        spreadsheet_token,
        sheet_id,
        rows,
        header_index,
        field_map,
        warning_days=refresh_warning_days,
        dry_run=dry_run,
    )

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
        match_key = phone if match_by == "phone" else name

        print(f"\n处理: {channel} | {name} | {phone or '(无手机号)'}")

        try:
            credit_totals = _fetch_account_credits(account, jimeng_cfg)
        except Exception as exc:
            mark = _fail_remark_text(exc)
            existing = record_index.get(match_key)
            print(f"  查询失败: {exc}")
            if existing and field_map.get("remark"):
                old_remark = _cell_text(
                    existing.get("fields", {}).get(field_map["remark"])
                )
                fields = {
                    field_map["remark"]: _with_status_remark(old_remark, mark),
                }
                if dry_run:
                    print(f"  [dry-run] 备注标记为: {fields[field_map['remark']]}")
                else:
                    feishu.update_record(
                        app_token, table_id, existing["record_id"], fields
                    )
                    print(f"  已标记: {fields[field_map['remark']]}")
            success_count += 1
            continue

        now_text = _now_text()
        existing = record_index.get(match_key)
        total_credit = _combined_credit(credit_totals)

        if existing:
            fields: dict[str, Any] = {}
            if "current_credit" in field_map:
                fields[field_map["current_credit"]] = total_credit
            if field_map.get("remark"):
                old_remark = _cell_text(
                    existing.get("fields", {}).get(field_map["remark"])
                )
                fields[field_map["remark"]] = _clear_status_remark(old_remark)
        else:
            fields = {
                field_map["channel"]: channel,
                field_map["name"]: name,
            }
            if phone:
                fields[field_map["phone"]] = phone
            if "current_credit" in field_map:
                fields[field_map["current_credit"]] = total_credit

        if field_map.get("updated_at"):
            fields[field_map["updated_at"]] = now_text

        summary = ", ".join(
            f"{PLATFORM_LABELS[k]}={v}" for k, v in credit_totals.items()
        )
        credit_text = f"当前积分={total_credit}（{summary}）" if len(credit_totals) > 1 else f"当前积分={total_credit}"

        if dry_run:
            print(f"  [dry-run] 写入字段: {fields} ({credit_text})")
            success_count += 1
            continue

        if existing:
            feishu.update_record(app_token, table_id, existing["record_id"], fields)
            print(f"  已更新: {credit_text}，更新时间: {now_text}")
        else:
            to_create.append({"fields": fields})
            print(f"  待新增记录: {credit_text}，更新时间: {now_text}")

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

    refresh_warning_days = int(feishu_cfg.get("refresh_warning_days", 7))

    if target["type"] == "spreadsheet":
        success_count = sync_spreadsheet(
            feishu,
            target,
            field_map,
            match_by,
            jimeng_cfg,
            dry_run,
            refresh_warning_days=refresh_warning_days,
        )
    else:
        success_count = sync_bitable(
            feishu, target, field_map, match_by, jimeng_cfg, dry_run
        )

    print(f"\n完成，共处理 {success_count} 个账号")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="同步即梦 / 小云雀 / LibTV 积分到飞书表格")
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
