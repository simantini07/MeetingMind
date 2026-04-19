"""Google Calendar OAuth + API helpers (single stored refresh token)."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


def _web_client_config() -> Optional[dict]:
    cid = os.getenv("GOOGLE_CALENDAR_CLIENT_ID", "").strip()
    secret = os.getenv("GOOGLE_CALENDAR_CLIENT_SECRET", "").strip()
    if not cid or not secret:
        return None
    return {
        "web": {
            "client_id": cid,
            "client_secret": secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def oauth_configured() -> bool:
    return bool(
        _web_client_config()
        and os.getenv("GOOGLE_CALENDAR_REDIRECT_URI", "").strip()
    )


def _redirect_uri() -> str:
    return os.getenv("GOOGLE_CALENDAR_REDIRECT_URI", "").strip()


def get_redirect_uri() -> Optional[str]:
    """Exact redirect_uri sent to Google — must match Authorized redirect URIs in Cloud Console."""
    ru = _redirect_uri()
    return ru or None


def make_flow() -> Flow:
    cfg = _web_client_config()
    if not cfg:
        raise ValueError("Google Calendar OAuth client id/secret missing")
    ru = _redirect_uri()
    if not ru:
        raise ValueError("GOOGLE_CALENDAR_REDIRECT_URI missing")
    # PKCE is on by default; we create a new Flow on callback without the prior
    # code_verifier → invalid_grant / Missing code verifier. Web + client secret
    # can use the classic auth-code flow without PKCE.
    return Flow.from_client_config(
        cfg,
        scopes=SCOPES,
        redirect_uri=ru,
        autogenerate_code_verifier=False,
    )


def authorization_url() -> str:
    flow = make_flow()
    url, _state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return url


def exchange_code(code: str) -> Credentials:
    flow = make_flow()
    flow.fetch_token(code=code)
    creds = flow.credentials
    if not creds.refresh_token:
        raise RuntimeError(
            "Google did not return a refresh token. In Google Account → Third-party access, "
            "remove MeetingMind and connect again (prompt=consent is required once)."
        )
    return creds


def credentials_from_refresh_token(refresh_token: str) -> Credentials:
    cfg = _web_client_config()
    if not cfg:
        raise ValueError("Google Calendar OAuth not configured")
    web = cfg["web"]
    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=web["token_uri"],
        client_id=web["client_id"],
        client_secret=web["client_secret"],
        scopes=SCOPES,
    )


def _parse_event_dt(ev: Dict[str, Any], key: str) -> Optional[datetime]:
    block = ev.get(key) or {}
    s = block.get("dateTime") or block.get("date")
    if not s:
        return None
    if block.get("date") and not block.get("dateTime"):
        from datetime import date as date_cls

        d = date_cls.fromisoformat(s)
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def list_now_and_upcoming(
    refresh_token: str, max_results: int = 25
) -> Dict[str, Any]:
    creds = credentials_from_refresh_token(refresh_token)
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(hours=6)).isoformat().replace("+00:00", "Z")
    out = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=time_min,
            maxResults=max(1, min(max_results, 50)),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    items = out.get("items") or []
    normalized: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for ev in items:
        st = _parse_event_dt(ev, "start")
        en = _parse_event_dt(ev, "end")
        rec = {
            "id": ev.get("id"),
            "summary": ev.get("summary") or "(no title)",
            "html_link": ev.get("htmlLink"),
            "start": st.isoformat() if st else None,
            "end": en.isoformat() if en else None,
        }
        normalized.append(rec)
        if st and en and st <= now < en:
            current = rec
    return {"events": normalized, "current_event": current}


def insert_calendar_event(
    refresh_token: str,
    summary: str,
    description: str,
    start: datetime,
    end: datetime,
    timezone_name: str,
    add_meet_link: bool = False,
) -> Dict[str, Any]:
    creds = credentials_from_refresh_token(refresh_token)
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = timezone.utc
        timezone_name = "UTC"

    start_local = start.astimezone(tz)
    end_local = end.astimezone(tz)

    body: Dict[str, Any] = {
        "summary": (summary or "Meeting")[:1024],
        "description": (description or "")[:8192],
        "start": {"dateTime": start_local.isoformat(), "timeZone": timezone_name},
        "end": {"dateTime": end_local.isoformat(), "timeZone": timezone_name},
    }
    if add_meet_link:
        body["conferenceData"] = {
            "createRequest": {
                "requestId": str(uuid.uuid4()),
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }

    insert_kw: Dict[str, Any] = dict(calendarId="primary", body=body)
    if add_meet_link:
        insert_kw["conferenceDataVersion"] = 1

    try:
        created = service.events().insert(**insert_kw).execute()
    except HttpError as e:
        raise RuntimeError(e.reason or str(e)) from e

    return {
        "id": created.get("id"),
        "html_link": created.get("htmlLink"),
        "hangout_link": created.get("hangoutLink"),
    }
