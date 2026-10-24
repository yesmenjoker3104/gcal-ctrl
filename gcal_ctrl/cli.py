import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta


_EVENT_ID_RE = re.compile(r"[A-Za-z0-9_@.\-]+")


_AUTH_KEYWORDS = (
    "Access denied",
    "No credentials",
    "auth login",
    "GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE",
    "invalid_grant",
    "Token has been expired or revoked",
    "insufficient authentication scopes",
)


def _find_gws() -> str:
    command = os.environ.get("GWS_COMMAND", "gws")
    found = shutil.which(command)
    if found:
        return found
    candidates = [
        os.path.expandvars(r"%APPDATA%\npm\gws.cmd"),
        os.path.expandvars(
            r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\OpenJS.NodeJS.LTS_Microsoft.Winget.Source_8wekyb3d8bbwe\node-v24.19.0-win-x64\gws.cmd"
        ),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    raise FileNotFoundError(
        "gws コマンドが見つかりません。settings.json の gws_command を設定するか、PATH に追加してください。"
    )


def _native_gws(exe: str) -> str:
    # npm の .cmd シムは cmd.exe を経由するため、JSON 内の & や | が実行されてしまう。同梱のネイティブ実行ファイルを直接使う
    if not exe.lower().endswith((".cmd", ".bat")):
        return exe
    native = os.path.join(os.path.dirname(exe), "node_modules", "@googleworkspace", "cli", "bin", "gws.exe")
    if os.path.exists(native):
        return native
    raise RuntimeError("gws.exe が見つかりません。GWS_COMMAND に gws.exe のパスを指定してください。")


def _check_event_id(event_id: str) -> str:
    if not _EVENT_ID_RE.fullmatch(event_id):
        raise RuntimeError("予定のIDの形式が正しくありません。")
    return event_id


def _run_gws(args: list[str]) -> str:
    exe = _native_gws(_find_gws())
    result = subprocess.run(
        [exe, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "gws がエラーを返しました").strip())
    return (result.stdout or "").strip()


def _is_auth_error(message: str) -> bool:
    return any(k in message for k in _AUTH_KEYWORDS)


def _ok(**kwargs) -> None:
    print(json.dumps({"ok": True, **kwargs}, ensure_ascii=True))


def _err(message: str, auth_required: bool = False) -> None:
    print(json.dumps({"ok": False, "error": message, "auth_required": auth_required}, ensure_ascii=True))


def _confirm(title: str, message: str) -> bool:
    """Ask the host to show a yes/no dialog. Returns False if the host gives no 'yes'."""
    print(json.dumps({"type": "confirm", "id": "confirm", "title": title, "message": message}, ensure_ascii=True), flush=True)
    line = sys.stdin.readline()
    if not line:
        return False
    try:
        reply = json.loads(line)
    except json.JSONDecodeError:
        return False
    return reply.get("type") == "confirm_response" and reply.get("confirmed") is True


_WEEKDAYS = "月火水木金土日"


def _agenda_rows(items: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for item in items:
        start_info = item.get("start") or {}
        end_info = item.get("end") or {}
        if "dateTime" in start_info:
            start_dt = datetime.fromisoformat(start_info["dateTime"]).astimezone()
            time_label = start_dt.strftime("%H:%M")
            if "dateTime" in end_info:
                end_dt = datetime.fromisoformat(end_info["dateTime"]).astimezone()
                time_label += " 〜 " + end_dt.strftime("%H:%M")
            day = start_dt.date()
        elif "date" in start_info:
            day = date.fromisoformat(start_info["date"])
            time_label = "終日"
        else:
            continue
        rows.append({
            "日付": f"{day.month}/{day.day}（{_WEEKDAYS[day.weekday()]}）",
            "時間": time_label,
            "予定": item.get("summary") or "（タイトルなし）",
            "場所": item.get("location") or "",
            "ID": item.get("id", ""),
        })
    return rows


def _event_label(event_id: str) -> str:
    try:
        raw = _run_gws(["calendar", "events", "get", "--params", json.dumps({"calendarId": "primary", "eventId": event_id})])
        payload = json.loads(raw) if raw else {}
        return payload.get("summary") or "（タイトルなし）"
    except Exception:
        return f"ID: {event_id}"


def _describe_event(event: dict) -> str:
    title = str(event.get("title") or "").strip() or "（タイトルなし）"
    start = str(event.get("start") or "")
    end = str(event.get("end") or "")
    if event.get("all_day"):
        when = f"{start[:10]} 終日"
    else:
        when = f"{start.replace('T', ' ')[:16]} 〜 {end.replace('T', ' ')[:16]}"
    suffix = "（繰り返し）" if event.get("rrule") else ""
    return f"・{title}：{when}{suffix}"


def cmd_agenda(args) -> None:
    try:
        tz = datetime.now().astimezone().tzinfo
        if args.start_date:
            start_day = datetime.fromisoformat(args.start_date).date()
            end_day = datetime.fromisoformat(args.end_date).date() if args.end_date else start_day + timedelta(days=1)
            if end_day <= start_day:
                end_day = start_day + timedelta(days=1)
            params: dict = {
                "calendarId": "primary",
                "timeMin": datetime(start_day.year, start_day.month, start_day.day, tzinfo=tz).isoformat(),
                "timeMax": datetime(end_day.year, end_day.month, end_day.day, tzinfo=tz).isoformat(),
                "singleEvents": True,
                "orderBy": "startTime",
            }
            if args.query:
                params["q"] = args.query
            raw = _run_gws(["calendar", "events", "list", "--params", json.dumps(params)])
        elif args.date:
            start_day = datetime.fromisoformat(args.date).date()
            end_day = start_day + timedelta(days=1)
            params = {
                "calendarId": "primary",
                "timeMin": datetime(start_day.year, start_day.month, start_day.day, tzinfo=tz).isoformat(),
                "timeMax": datetime(end_day.year, end_day.month, end_day.day, tzinfo=tz).isoformat(),
                "singleEvents": True,
                "orderBy": "startTime",
            }
            if args.query:
                params["q"] = args.query
            raw = _run_gws(["calendar", "events", "list", "--params", json.dumps(params)])
        elif args.query:
            params = {
                "calendarId": "primary",
                "q": args.query,
                "singleEvents": True,
                "orderBy": "startTime",
            }
            raw = _run_gws(["calendar", "events", "list", "--params", json.dumps(params)])
        else:
            today = datetime.now().astimezone().date()
            params = {
                "calendarId": "primary",
                "timeMin": datetime(today.year, today.month, today.day, tzinfo=tz).isoformat(),
                "singleEvents": True,
                "orderBy": "startTime",
                "maxResults": 250,
            }
            raw = _run_gws(["calendar", "events", "list", "--params", json.dumps(params)])

        payload = json.loads(raw) if raw else {}
        items = payload.get("items", []) if isinstance(payload, dict) else []
        rows = _agenda_rows(items)
        _ok(rows=rows, count=len(rows))
    except RuntimeError as exc:
        msg = str(exc)
        _err(msg, auth_required=_is_auth_error(msg))
    except Exception as exc:
        _err(str(exc))


def _create_single(event: dict) -> None:
    title = str(event.get("title", "")).strip()
    if not title:
        raise RuntimeError("予定名が空です。")
    start = str(event.get("start", ""))
    end = str(event.get("end", ""))
    description = str(event.get("description", ""))
    all_day = bool(event.get("all_day", False))

    if all_day:
        start_date = datetime.fromisoformat(start).date()
        end_date = datetime.fromisoformat(end).date()
        if end_date <= start_date:
            end_date = start_date + timedelta(days=1)
        gws_event: dict = {
            "summary": title,
            "start": {"date": start_date.isoformat()},
            "end": {"date": end_date.isoformat()},
        }
    else:
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
        if end_dt <= start_dt:
            raise RuntimeError("予定の終了日時は開始日時より後にしてください。")
        gws_event = {
            "summary": title,
            "start": {"dateTime": start, "timeZone": "Asia/Tokyo"},
            "end": {"dateTime": end, "timeZone": "Asia/Tokyo"},
        }
    if description:
        gws_event["description"] = description
    rrule = event.get("rrule")
    if rrule:
        gws_event["recurrence"] = [f"RRULE:{rrule}"]

    _run_gws([
        "calendar", "events", "insert",
        "--params", json.dumps({"calendarId": "primary"}),
        "--json", json.dumps(gws_event, ensure_ascii=False),
    ])


def cmd_create(args) -> None:
    try:
        if args.events_json:
            events = json.loads(args.events_json)
            if not isinstance(events, list):
                events = [events]
        else:
            events = [{
                "title": args.title or "",
                "start": args.start or "",
                "end": args.end or "",
                "description": args.description or "",
                "all_day": args.all_day,
                "rrule": args.rrule or None,
            }]
        if not events or not all(isinstance(e, dict) for e in events):
            raise RuntimeError("予定の指定が正しくありません。")
        if not _confirm("予定の作成", "以下の予定を作成しますか？\n" + "\n".join(_describe_event(e) for e in events)):
            _ok(message="予定の作成をキャンセルしました。")
            return
        created = 0
        for event in events:
            _create_single(event)
            created += 1
        _ok(message=f"{created} 件の予定を作成しました。")
    except RuntimeError as exc:
        msg = str(exc)
        _err(msg, auth_required=_is_auth_error(msg))
    except Exception as exc:
        _err(str(exc))


def cmd_delete(args) -> None:
    try:
        if not args.event_id:
            raise RuntimeError("削除する予定のIDが指定されていません。")
        _check_event_id(args.event_id)
        label = _event_label(args.event_id)
        if not _confirm("予定の削除", f"「{label}」を削除しますか？"):
            _ok(message="予定の削除をキャンセルしました。")
            return
        _run_gws([
            "calendar", "events", "delete",
            "--params", json.dumps({"calendarId": "primary", "eventId": args.event_id}),
        ])
        _ok(message="予定を削除しました。")
    except RuntimeError as exc:
        msg = str(exc)
        _err(msg, auth_required=_is_auth_error(msg))
    except Exception as exc:
        _err(str(exc))


def cmd_update(args) -> None:
    try:
        patch: dict = {}
        if args.title is not None:
            patch["summary"] = args.title
        if args.start is not None:
            patch["start"] = {"dateTime": args.start, "timeZone": "Asia/Tokyo"}
        if args.end is not None:
            patch["end"] = {"dateTime": args.end, "timeZone": "Asia/Tokyo"}
        if args.description is not None:
            patch["description"] = args.description
        if not patch:
            _err("変更する内容が指定されていません。")
            return
        if not args.event_id:
            raise RuntimeError("変更する予定のIDが指定されていません。")
        _check_event_id(args.event_id)
        label = _event_label(args.event_id)
        changes = []
        if args.title is not None:
            changes.append(f"・タイトル：{args.title}")
        if args.start is not None:
            changes.append(f"・開始：{args.start}")
        if args.end is not None:
            changes.append(f"・終了：{args.end}")
        if args.description is not None:
            changes.append(f"・説明：{args.description}")
        if not _confirm("予定の変更", f"「{label}」を次の内容に変更しますか？\n" + "\n".join(changes)):
            _ok(message="予定の変更をキャンセルしました。")
            return
        _run_gws([
            "calendar", "events", "patch",
            "--params", json.dumps({"calendarId": "primary", "eventId": args.event_id}),
            "--json", json.dumps(patch, ensure_ascii=False),
        ])
        _ok(message="予定を変更しました。")
    except RuntimeError as exc:
        msg = str(exc)
        _err(msg, auth_required=_is_auth_error(msg))
    except Exception as exc:
        _err(str(exc))


def cmd_free_busy(args) -> None:
    try:
        tz = datetime.now().astimezone().tzinfo
        start_day = datetime.fromisoformat(args.start_date).date()
        end_day = (
            datetime.fromisoformat(args.end_date).date()
            if args.end_date
            else start_day + timedelta(days=1)
        )
        time_min = datetime(start_day.year, start_day.month, start_day.day, tzinfo=tz).isoformat()
        time_max = datetime(end_day.year, end_day.month, end_day.day, tzinfo=tz).isoformat()
        raw = _run_gws([
            "calendar", "freebusy", "query",
            "--json", json.dumps(
                {"timeMin": time_min, "timeMax": time_max, "items": [{"id": "primary"}]},
                ensure_ascii=False,
            ),
        ])
        payload = json.loads(raw) if raw else {}
        calendars = payload.get("calendars", {})
        primary = calendars.get("primary", {})
        busy_periods = primary.get("busy", [])

        busy_list = []
        for period in busy_periods:
            s = datetime.fromisoformat(period["start"])
            e = datetime.fromisoformat(period["end"])
            busy_list.append({
                "date": s.strftime("%Y-%m-%d"),
                "start": s.strftime("%H:%M"),
                "end": e.strftime("%H:%M"),
            })

        # Compute free slots (09:00-20:00 range) per day
        free_slots: list[dict] = []
        current_day = start_day
        while current_day < end_day:
            day_str = current_day.strftime("%Y-%m-%d")
            day_busy = sorted(
                [b for b in busy_list if b["date"] == day_str],
                key=lambda x: x["start"],
            )
            cursor = datetime(current_day.year, current_day.month, current_day.day, 9, 0)
            day_end = datetime(current_day.year, current_day.month, current_day.day, 20, 0)
            for b in day_busy:
                b_start = datetime.strptime(f"{day_str} {b['start']}", "%Y-%m-%d %H:%M")
                b_end = datetime.strptime(f"{day_str} {b['end']}", "%Y-%m-%d %H:%M")
                if cursor < b_start:
                    free_slots.append({
                        "date": day_str,
                        "start": cursor.strftime("%H:%M"),
                        "end": b_start.strftime("%H:%M"),
                    })
                if b_end > cursor:
                    cursor = b_end
            if cursor < day_end:
                free_slots.append({
                    "date": day_str,
                    "start": cursor.strftime("%H:%M"),
                    "end": day_end.strftime("%H:%M"),
                })
            current_day += timedelta(days=1)

        _ok(busy=busy_list, free_slots=free_slots, summary=f"空き時間 {len(free_slots)} 件")
    except RuntimeError as exc:
        msg = str(exc)
        _err(msg, auth_required=_is_auth_error(msg))
    except Exception as exc:
        _err(str(exc))


def cmd_remind(_args) -> None:
    try:
        now = datetime.now().astimezone()
        window_end = now + timedelta(minutes=15)
        params = {
            "calendarId": "primary",
            "timeMin": now.isoformat(),
            "timeMax": window_end.isoformat(),
            "singleEvents": True,
            "orderBy": "startTime",
        }
        raw = _run_gws(["calendar", "events", "list", "--params", json.dumps(params)])
        payload = json.loads(raw) if raw else {}
        notices: list[dict] = []
        for item in payload.get("items", []) if isinstance(payload, dict) else []:
            start_raw = (item.get("start") or {}).get("dateTime")
            if not start_raw:
                continue
            start_dt = datetime.fromisoformat(start_raw).astimezone()
            if not (now <= start_dt <= window_end):
                continue
            title = item.get("summary") or "（タイトルなし）"
            notices.append({
                "key": f"{item.get('id', '')}:{start_raw}",
                "text": f"まもなく予定: {title}（{start_dt.strftime('%H:%M')}〜）",
            })
        _ok(notices=notices)
    except RuntimeError as exc:
        msg = str(exc)
        _err(msg, auth_required=_is_auth_error(msg))
    except Exception as exc:
        _err(str(exc))


def cmd_auth(_args) -> None:
    try:
        exe = _native_gws(_find_gws())
        import tempfile
        gcloud_bin = os.path.expandvars(r"%LOCALAPPDATA%\Google\Cloud SDK\google-cloud-sdk\bin")
        bat = tempfile.NamedTemporaryFile(suffix=".bat", delete=False, mode="w", encoding="utf-8")
        bat.write(
            f"@echo off\n"
            f"set PATH={gcloud_bin};%PATH%\n"
            f'"{exe}" auth login --scopes https://www.googleapis.com/auth/calendar\n'
            f"pause\n"
        )
        bat.close()
        os.startfile(bat.name)
        _ok()
    except Exception as exc:
        _err(str(exc))


def main() -> None:
    parser = argparse.ArgumentParser(prog="GCalCtrlCli", description="Google Calendar CLI for assist-chat plugin")
    sub = parser.add_subparsers(dest="command", required=True)

    p_agenda = sub.add_parser("agenda", help="予定一覧の取得")
    p_agenda.add_argument("--date", help="対象日 (YYYY-MM-DD)")
    p_agenda.add_argument("--start-date", dest="start_date", help="期間開始日 (YYYY-MM-DD)")
    p_agenda.add_argument("--end-date", dest="end_date", help="期間終了日 (YYYY-MM-DD)")
    p_agenda.add_argument("--query", help="検索語")
    p_agenda.set_defaults(func=cmd_agenda)

    p_create = sub.add_parser("create", help="予定の作成")
    p_create.add_argument("--title")
    p_create.add_argument("--start")
    p_create.add_argument("--end")
    p_create.add_argument("--description", default="")
    p_create.add_argument("--all-day", dest="all_day", action="store_true")
    p_create.add_argument("--events-json", dest="events_json", help="JSON array of event objects")
    p_create.add_argument("--rrule", help="繰り返しルール (例: FREQ=WEEKLY;BYDAY=MO)")
    p_create.set_defaults(func=cmd_create)

    p_update = sub.add_parser("update", help="予定の更新")
    p_update.add_argument("--event-id", dest="event_id")
    p_update.add_argument("--title")
    p_update.add_argument("--start")
    p_update.add_argument("--end")
    p_update.add_argument("--description")
    p_update.set_defaults(func=cmd_update)

    p_fb = sub.add_parser("free-busy", help="空き時間の確認")
    p_fb.add_argument("--start-date", dest="start_date", required=True)
    p_fb.add_argument("--end-date", dest="end_date")
    p_fb.set_defaults(func=cmd_free_busy)

    p_delete = sub.add_parser("delete", help="予定の削除")
    p_delete.add_argument("--event-id", dest="event_id")
    p_delete.set_defaults(func=cmd_delete)

    p_remind = sub.add_parser("remind", help="直近の予定のリマインド通知")
    p_remind.set_defaults(func=cmd_remind)

    p_auth = sub.add_parser("auth", help="Google認証")
    p_auth.set_defaults(func=cmd_auth)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
