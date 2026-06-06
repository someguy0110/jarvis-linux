"""
JARVIS Calendar Access — read Apple Calendar via AppleScript.

Strategy: fetch all events per-calendar in parallel (bulk property access),
filter dates in Python. Results cached and refreshed in background.
"""

import asyncio
import logging
import os
import sys
import time as _time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

log = logging.getLogger("jarvis.calendar")

# macOS: Apple Calendar via AppleScript
# Linux: CalDAV via HTTP (optional)
_IS_DARWIN = sys.platform == "darwin"

# CalDAV config (Linux/Windows)
_caldav_urls_env = os.getenv("CALDAV_CALENDAR_URLS", "").strip()
CALDAV_CALENDAR_URLS: list[str] = [u.strip() for u in _caldav_urls_env.split(",") if u.strip()]
CALDAV_URL = os.getenv("CALDAV_URL", "").strip()
CALDAV_USERNAME = os.getenv("CALDAV_USERNAME", "").strip()
CALDAV_PASSWORD = os.getenv("CALDAV_PASSWORD", "").strip()

# Calendars to scan — set CALENDAR_ACCOUNTS env var to a comma-separated list,
# or leave empty to auto-discover ALL calendars from Apple Calendar.
_calendar_accounts_env = os.getenv("CALENDAR_ACCOUNTS", "")
USER_CALENDARS: list[str] = [
    a.strip() for a in _calendar_accounts_env.split(",") if a.strip()
] if _calendar_accounts_env.strip() else []

_auto_discovered = False

# Cache: refreshed in background, never blocks responses
_event_cache: list[dict] = []
_cache_time: float = 0
_calendar_launched = False

# Per-calendar AppleScript: bulk property access (fast), no `whose` clause
_BULK_SCRIPT = '''
tell application "Calendar"
    set cal to calendar "{cal_name}"
    set dateList to start date of every event of cal
    set summaryList to summary of every event of cal
    set allDayList to allday event of every event of cal
    set output to ""
    repeat with i from 1 to count of dateList
        set output to output & ((item i of dateList) as string) & "|||" & (item i of summaryList) & "|||" & (item i of allDayList) & linefeed
    end repeat
    return output
end tell
'''


async def _ensure_calendar_running():
    """Launch Calendar.app if not already running."""
    global _calendar_launched
    if not _IS_DARWIN:
        return
    if _calendar_launched:
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            "open", "-a", "Calendar", "-g",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=5)
        await asyncio.sleep(2)
        _calendar_launched = True
        log.info("Calendar.app launched")
    except Exception as e:
        log.warning(f"Failed to launch Calendar: {e}")


async def _fetch_calendar_events(cal_name: str, timeout: float = 12.0) -> list[dict]:
    """Fetch all events from one calendar, filter to today in Python."""
    script = _BULK_SCRIPT.replace("{cal_name}", cal_name)
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            return []

        raw = stdout.decode().strip()
        if not raw:
            return []

        # Parse and filter to today
        now = datetime.now()
        today_date = now.date()
        events = []

        for line in raw.split("\n"):
            parts = line.strip().split("|||")
            if len(parts) < 3:
                continue
            date_str = parts[0].strip()
            title = parts[1].strip()
            all_day = parts[2].strip().lower() == "true"

            # Parse AppleScript date: "Wednesday, March 18, 2026 at 2:00:00 PM"
            try:
                parsed = _parse_applescript_date(date_str)
                if parsed and parsed.date() == today_date:
                    time_str = "ALL_DAY" if all_day else parsed.strftime("%-I:%M %p")
                    events.append({
                        "calendar": cal_name,
                        "title": title,
                        "start": time_str,
                        "start_dt": parsed,
                        "all_day": all_day,
                    })
            except Exception:
                continue

        return events

    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        log.debug(f"Calendar {cal_name} timed out")
        return []
    except Exception as e:
        log.debug(f"Calendar {cal_name} error: {e}")
        return []


def _parse_applescript_date(s: str) -> datetime | None:
    """Parse 'Wednesday, March 18, 2026 at 2:00:00 PM' to datetime."""
    # Remove day name prefix
    if ", " in s:
        s = s.split(", ", 1)[1]
    # Try common formats
    for fmt in [
        "%B %d, %Y at %I:%M:%S %p",
        "%B %d, %Y at %H:%M:%S",
    ]:
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None


async def refresh_cache():
    """Refresh the event cache. Called from background loop."""
    global _event_cache, _cache_time, USER_CALENDARS, _auto_discovered
    if not _IS_DARWIN:
        start = _time.time()
        events = await _fetch_caldav_events_for_today()
        events.sort(key=lambda e: (not e["all_day"], e.get("start_dt") or datetime.max))
        _event_cache = events
        _cache_time = _time.time()
        elapsed = _time.time() - start
        log.info(f"Calendar cache refreshed: {len(events)} events today ({elapsed:.1f}s)")
        return

    await _ensure_calendar_running()

    # Auto-discover calendars if none configured
    if not USER_CALENDARS and not _auto_discovered:
        _auto_discovered = True
        discovered = await get_calendar_names()
        if discovered:
            USER_CALENDARS = discovered
            log.info(f"Auto-discovered calendars: {USER_CALENDARS}")
        else:
            log.warning("No calendars discovered — set CALENDAR_ACCOUNTS env var")
            return

    if not USER_CALENDARS:
        return

    start = _time.time()
    # Fetch calendars in small batches — Calendar.app chokes on too many parallel osascript
    all_events = []
    batch_size = 2
    for i in range(0, len(USER_CALENDARS), batch_size):
        batch = USER_CALENDARS[i:i + batch_size]
        results = await asyncio.gather(
            *[_fetch_calendar_events(cal, timeout=15) for cal in batch],
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, list):
                all_events.extend(result)

    # Sort by time (all-day first, then by start time)
    all_events.sort(key=lambda e: (not e["all_day"], e.get("start_dt") or datetime.max))

    _event_cache = all_events
    _cache_time = _time.time()
    elapsed = _time.time() - start
    log.info(f"Calendar cache refreshed: {len(all_events)} events today ({elapsed:.1f}s)")


async def get_todays_events() -> list[dict]:
    """Get today's events from cache. Returns cached data immediately."""
    if not _event_cache and _cache_time == 0:
        # First call — try a quick refresh
        await refresh_cache()
    return _event_cache


async def get_upcoming_events(hours: int = 4) -> list[dict]:
    """Get events in the next N hours from cache."""
    events = await get_todays_events()
    now = datetime.now()
    cutoff = now + timedelta(hours=hours)
    return [
        e for e in events
        if not e["all_day"] and e.get("start_dt") and now <= e["start_dt"] <= cutoff
    ]


async def get_next_event() -> dict | None:
    """Get the single next upcoming event."""
    events = await get_upcoming_events(hours=24)
    return events[0] if events else None


async def get_calendar_names() -> list[str]:
    """Get list of all calendar names."""
    if not _IS_DARWIN:
        discovered = await _discover_caldav_calendars()
        return [c["name"] for c in discovered]

    await _ensure_calendar_running()
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e",
            'tell application "Calendar" to return name of every calendar',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode == 0:
            return [c.strip() for c in stdout.decode().strip().split(",") if c.strip()]
    except Exception:
        pass
    return []


def _caldav_configured() -> bool:
    if not CALDAV_USERNAME or not CALDAV_PASSWORD:
        return False
    return bool(CALDAV_CALENDAR_URLS or CALDAV_URL)


async def _discover_caldav_calendars() -> list[dict]:
    if not _caldav_configured():
        return []

    if CALDAV_CALENDAR_URLS:
        return [{"name": url.rstrip("/").split("/")[-1] or "Calendar", "url": url} for url in CALDAV_CALENDAR_URLS]

    import httpx
    from xml.etree import ElementTree as ET

    headers = {
        "Depth": "1",
        "Content-Type": "application/xml; charset=utf-8",
    }
    body = """<?xml version="1.0" encoding="utf-8" ?>
<d:propfind xmlns:d="DAV:" xmlns:cs="http://calendarserver.org/ns/">
  <d:prop>
    <d:displayname />
    <d:resourcetype />
  </d:prop>
</d:propfind>"""

    try:
        async with httpx.AsyncClient(auth=httpx.BasicAuth(CALDAV_USERNAME, CALDAV_PASSWORD), timeout=10.0) as client:
            resp = await client.request("PROPFIND", CALDAV_URL, headers=headers, content=body)
            if resp.status_code not in (207, 200):
                return []

        root = ET.fromstring(resp.text)
        ns = {
            "d": "DAV:",
            "c": "urn:ietf:params:xml:ns:caldav",
        }

        calendars: list[dict] = []
        for r in root.findall(".//d:response", ns):
            href = r.findtext("d:href", default="", namespaces=ns).strip()
            display = r.findtext(".//d:displayname", default="", namespaces=ns).strip()
            resourcetype = r.find(".//d:resourcetype", ns)
            if resourcetype is None:
                continue
            if resourcetype.find("c:calendar", ns) is None:
                continue
            url = urljoin(CALDAV_URL, href)
            name = display or url.rstrip("/").split("/")[-1] or "Calendar"
            calendars.append({"name": name, "url": url})

        return calendars
    except Exception:
        return []


async def _fetch_caldav_events_for_today() -> list[dict]:
    if not _caldav_configured():
        return []

    import httpx
    from datetime import timezone
    from icalendar import Calendar
    from xml.etree import ElementTree as ET

    calendars = await _discover_caldav_calendars()
    if not calendars:
        return []

    now_local = datetime.now().astimezone()
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)

    start_str = start_utc.strftime("%Y%m%dT%H%M%SZ")
    end_str = end_utc.strftime("%Y%m%dT%H%M%SZ")

    headers = {
        "Depth": "1",
        "Content-Type": "application/xml; charset=utf-8",
    }
    report = f"""<?xml version="1.0" encoding="utf-8" ?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <d:getetag />
    <c:calendar-data />
  </d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VEVENT">
        <c:time-range start="{start_str}" end="{end_str}" />
      </c:comp-filter>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"""

    events: list[dict] = []
    ns = {
        "d": "DAV:",
        "c": "urn:ietf:params:xml:ns:caldav",
    }

    async with httpx.AsyncClient(auth=httpx.BasicAuth(CALDAV_USERNAME, CALDAV_PASSWORD), timeout=20.0) as client:
        for cal in calendars:
            try:
                resp = await client.request("REPORT", cal["url"], headers=headers, content=report)
                if resp.status_code not in (207, 200):
                    continue

                root = ET.fromstring(resp.text)
                for cd in root.findall(".//c:calendar-data", ns):
                    ical = (cd.text or "").strip()
                    if not ical:
                        continue
                    try:
                        parsed = Calendar.from_ical(ical)
                    except Exception:
                        continue

                    for component in parsed.walk():
                        if component.name != "VEVENT":
                            continue
                        summary = str(component.get("SUMMARY") or "").strip()
                        dtstart = component.get("DTSTART")
                        if not dtstart:
                            continue
                        dt_val = dtstart.dt

                        all_day = not hasattr(dt_val, "hour")
                        if all_day:
                            start_dt = datetime(dt_val.year, dt_val.month, dt_val.day, tzinfo=now_local.tzinfo)
                            time_str = "ALL_DAY"
                        else:
                            start_dt = dt_val
                            if start_dt.tzinfo is None:
                                start_dt = start_dt.replace(tzinfo=now_local.tzinfo)
                            start_dt = start_dt.astimezone(now_local.tzinfo)
                            time_str = start_dt.strftime("%-I:%M %p")

                        if start_dt.date() != now_local.date():
                            continue

                        events.append({
                            "calendar": cal["name"],
                            "title": summary or "(No title)",
                            "start": time_str,
                            "start_dt": start_dt,
                            "all_day": all_day,
                        })
            except Exception:
                continue

    return events


def format_events_for_context(events: list[dict]) -> str:
    """Format events as context for the LLM."""
    if not events:
        return "No events scheduled today."

    lines = []
    for evt in events:
        if evt.get("all_day"):
            entry = f"  All day — {evt['title']}"
        else:
            entry = f"  {evt['start']} — {evt['title']}"
        if evt.get("calendar"):
            entry += f" [{evt['calendar']}]"
        lines.append(entry)

    return "\n".join(lines)


def format_schedule_summary(events: list[dict]) -> str:
    """Format a brief voice-friendly summary of the schedule."""
    if not events:
        return "Your schedule is clear today, sir."

    count = len(events)
    if count == 1:
        evt = events[0]
        if evt.get("all_day"):
            return f"You have one all-day event: {evt['title']}."
        return f"You have one event: {evt['title']} at {evt['start']}."

    summaries = []
    for evt in events[:5]:
        if evt.get("all_day"):
            summaries.append(f"{evt['title']} all day")
        else:
            summaries.append(f"{evt['title']} at {evt['start']}")

    result = f"You have {count} events today. "
    result += ". ".join(summaries[:3])
    if count > 3:
        result += f". And {count - 3} more."
    return result
