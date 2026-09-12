import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta


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
    # WinGet default installation path for gws
    candidate = os.path.expandvars(
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\OpenJS.NodeJS.LTS_Microsoft.Winget.Source_8wekyb3d8bbwe\node-v24.19.0-win-x64\gws.cmd"
    )
    if os.path.exists(candidate):
        return candidate
    raise FileNotFoundError(
        "gws コマンドが見つかりません。settings.json の gws_command を設定するか、PATH に追加してください。"
    )


def _run_gws(args: list[str]) -> str:
    exe = _find_gws()
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
            raw = _run_gws(["calendar", "+agenda"])

        payload = json.loads(raw) if raw else {}
        items = payload.get("items", []) if isinstance(payload, dict) else []
        _ok(items=items, count=len(items))
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
            }]
        created = 0
        for event in events:
            _create_single(event)
            created += 1
        _ok(created=created)
    except RuntimeError as exc:
        msg = str(exc)
        _err(msg, auth_required=_is_auth_error(msg))
    except Exception as exc:
        _err(str(exc))


def cmd_delete(args) -> None:
    try:
        _run_gws([
            "calendar", "events", "delete",
            "--params", json.dumps({"calendarId": "primary", "eventId": args.event_id}),
        ])
        _ok()
    except RuntimeError as exc:
        msg = str(exc)
        _err(msg, auth_required=_is_auth_error(msg))
    except Exception as exc:
        _err(str(exc))


def cmd_auth(_args) -> None:
    try:
        exe = _find_gws()
        import tempfile
        gcloud_bin = os.path.expandvars(r"%LOCALAPPDATA%\Google\Cloud SDK\google-cloud-sdk\bin")
        bat = tempfile.NamedTemporaryFile(suffix=".bat", delete=False, mode="w", encoding="utf-8")
        bat.write(
            f"@echo off\n"
            f"set PATH={gcloud_bin};%PATH%\n"
            f'"{exe}" auth setup --login\n'
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
    p_create.set_defaults(func=cmd_create)

    p_delete = sub.add_parser("delete", help="予定の削除")
    p_delete.add_argument("--event-id", dest="event_id", required=True)
    p_delete.set_defaults(func=cmd_delete)

    p_auth = sub.add_parser("auth", help="Google認証")
    p_auth.set_defaults(func=cmd_auth)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
