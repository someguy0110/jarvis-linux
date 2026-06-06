"""
JARVIS Apple Notes Access — READ + CREATE ONLY.

Can read existing notes and create new ones.
CANNOT edit or delete existing notes (safety).
"""

import asyncio
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

log = logging.getLogger("jarvis.notes")

_IS_DARWIN = sys.platform == "darwin"


def _notes_root() -> Path:
    raw = os.getenv("NOTES_DIR", "").strip()
    root = Path(raw).expanduser() if raw else (Path.home() / "jarvis-notes")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _iter_note_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in (".md", ".txt"):
            files.append(p)
    return files


def _slugify_filename(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "note"


async def _run_notes_script(script: str, timeout: float = 10) -> str:
    """Run an AppleScript against Notes.app."""
    if not _IS_DARWIN:
        return ""
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            log.warning(f"Notes script failed: {stderr.decode()[:200]}")
            return ""
        return stdout.decode().strip()
    except asyncio.TimeoutError:
        log.warning("Notes script timed out")
        return ""
    except Exception as e:
        log.warning(f"Notes script error: {e}")
        return ""


async def get_recent_notes(count: int = 10) -> list[dict]:
    """Get most recent notes (title + creation date)."""
    if not _IS_DARWIN:
        root = _notes_root()
        files = _iter_note_files(root)
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        out: list[dict] = []
        for p in files[:count]:
            ts = datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
            folder = str(p.parent.relative_to(root)) if p.parent != root else "Notes"
            out.append({"title": p.stem, "date": ts, "folder": folder})
        return out

    script = f'''
tell application "Notes"
    set output to ""
    set allNotes to every note
    set limit to count of allNotes
    if limit > {count} then set limit to {count}
    repeat with i from 1 to limit
        set n to item i of allNotes
        set nName to name of n
        set nDate to creation date of n as string
        set nFolder to name of container of n
        set output to output & nName & "|||" & nDate & "|||" & nFolder & linefeed
    end repeat
    return output
end tell
'''
    raw = await _run_notes_script(script, timeout=15)
    if not raw:
        return []
    notes = []
    for line in raw.split("\n"):
        parts = line.strip().split("|||")
        if len(parts) >= 3:
            notes.append({
                "title": parts[0].strip(),
                "date": parts[1].strip(),
                "folder": parts[2].strip(),
            })
    return notes


async def read_note(title_match: str) -> dict | None:
    """Read a note by title (partial match). Returns title + body."""
    if not _IS_DARWIN:
        root = _notes_root()
        query = title_match.strip().lower()
        for p in _iter_note_files(root):
            if query and query in p.stem.lower():
                body = p.read_text(errors="ignore")
                if len(body) > 3000:
                    body = body[:3000]
                return {"title": p.stem, "body": body.strip()}
        return None

    escaped = title_match.replace('"', '\\"')
    script = f'''
tell application "Notes"
    set allNotes to every note
    repeat with n in allNotes
        if name of n contains "{escaped}" then
            set nName to name of n
            set nBody to plaintext of n
            -- Truncate very long notes
            if length of nBody > 3000 then
                set nBody to text 1 thru 3000 of nBody
            end if
            return nName & "|||" & nBody
        end if
    end repeat
    return ""
end tell
'''
    raw = await _run_notes_script(script, timeout=10)
    if not raw or "|||" not in raw:
        return None
    title, _, body = raw.partition("|||")
    return {"title": title.strip(), "body": body.strip()}


async def search_notes_apple(query: str, count: int = 5) -> list[dict]:
    """Search notes by title keyword."""
    if not _IS_DARWIN:
        root = _notes_root()
        q = query.strip().lower()
        matches: list[dict] = []
        for p in _iter_note_files(root):
            if len(matches) >= count:
                break
            try:
                if q in p.stem.lower() or q in p.read_text(errors="ignore").lower():
                    ts = datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
                    matches.append({"title": p.stem, "date": ts})
            except Exception:
                continue
        return matches

    escaped = query.replace('"', '\\"')
    script = f'''
tell application "Notes"
    set output to ""
    set foundCount to 0
    set allNotes to every note
    repeat with n in allNotes
        if foundCount >= {count} then exit repeat
        if name of n contains "{escaped}" then
            set output to output & name of n & "|||" & (creation date of n as string) & linefeed
            set foundCount to foundCount + 1
        end if
    end repeat
    return output
end tell
'''
    raw = await _run_notes_script(script, timeout=15)
    if not raw:
        return []
    notes = []
    for line in raw.split("\n"):
        parts = line.strip().split("|||")
        if len(parts) >= 2:
            notes.append({"title": parts[0].strip(), "date": parts[1].strip()})
    return notes


async def create_apple_note(title: str, body: str, folder: str = "Notes") -> bool:
    """Create a new note in Apple Notes with HTML support for formatting.

    Supports checklist items: lines starting with "- [ ]" or "- [x]" become checkboxes.
    """
    if not _IS_DARWIN:
        root = _notes_root()
        folder_name = folder.strip() or "Notes"
        folder_path = root / folder_name
        folder_path.mkdir(parents=True, exist_ok=True)

        stem = _slugify_filename(title)[:80]
        path = folder_path / f"{stem}.md"
        if path.exists():
            suffix = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = folder_path / f"{stem}-{suffix}.md"

        try:
            content = body if body.endswith("\n") else (body + "\n")
            if title.strip():
                content = f"# {title.strip()}\n\n{content}"
            path.write_text(content)
            log.info(f"Created note file: {path}")
            return True
        except Exception:
            return False

    # Convert markdown-style checklists to HTML
    html_body = _body_to_html(body)

    escaped_title = title.replace('"', '\\"')
    escaped_body = html_body.replace('"', '\\"')
    escaped_folder = folder.replace('"', '\\"')
    script = f'''
tell application "Notes"
    tell folder "{escaped_folder}"
        make new note with properties {{name:"{escaped_title}", body:"{escaped_body}"}}
    end tell
    return "OK"
end tell
'''
    result = await _run_notes_script(script, timeout=10)
    if result == "OK":
        log.info(f"Created Apple Note: {title}")
        return True
    return False


def _body_to_html(body: str) -> str:
    """Convert plain text / markdown to HTML for Apple Notes.

    Supports:
    - Checklist items: "- [ ] task" or "- [x] task" → checkbox
    - Bullet points: "- item" → bullet
    - Numbered lists: "1. item" → numbered
    - Plain text → paragraphs
    """
    import re
    lines = body.split("\n")
    html_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            html_lines.append("<br>")
        elif re.match(r"^-\s*\[x\]\s*", stripped, re.IGNORECASE):
            text = re.sub(r"^-\s*\[x\]\s*", "", stripped, flags=re.IGNORECASE)
            html_lines.append(f'<div><input type="checkbox" checked="checked"> {text}</div>')
        elif re.match(r"^-\s*\[\s?\]\s*", stripped):
            text = re.sub(r"^-\s*\[\s?\]\s*", "", stripped)
            html_lines.append(f'<div><input type="checkbox"> {text}</div>')
        elif re.match(r"^[-*+]\s+", stripped):
            text = re.sub(r"^[-*+]\s+", "", stripped)
            html_lines.append(f"<div>• {text}</div>")
        elif re.match(r"^\d+\.\s+", stripped):
            text = re.sub(r"^\d+\.\s+", "", stripped)
            html_lines.append(f"<div>{stripped}</div>")
        elif stripped.startswith("#"):
            text = re.sub(r"^#+\s*", "", stripped)
            html_lines.append(f"<h2>{text}</h2>")
        else:
            html_lines.append(f"<div>{stripped}</div>")

    return "\n".join(html_lines)


async def get_note_folders() -> list[str]:
    """Get list of note folder names."""
    if not _IS_DARWIN:
        root = _notes_root()
        folders = ["Notes"]
        for p in sorted(root.iterdir()):
            if p.is_dir() and not p.name.startswith("."):
                folders.append(p.name)
        return folders

    script = '''
tell application "Notes"
    set output to ""
    repeat with f in every folder
        set output to output & name of f & linefeed
    end repeat
    return output
end tell
'''
    raw = await _run_notes_script(script)
    return [f.strip() for f in raw.split("\n") if f.strip()]
