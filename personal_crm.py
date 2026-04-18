"""
Personal CRM built on Ara — Supabase-backed.

Keeps track of people you know, notes about them, and important dates.
You can feed it meeting notes (via iMessage / API call) and it will
extract contacts, notes, and dates, then sync birthdays/follow-ups to
your calendar via the Google Calendar connector. On scheduled runs it
sends iMessage reminders for upcoming meetings with CRM notes on the
attendees.

HOW TO RUN:
  1) Create a Supabase project at https://supabase.com (free tier).
  2) Open the SQL editor, paste schema.sql, and run it.
  3) In Supabase → Settings → API, grab:
       - Project URL              -> SUPABASE_URL
       - service_role secret key  -> SUPABASE_KEY
  4) In app.ara.so → your personal-crm agent → secrets, set those two.
  5) pip install ara-sdk
  6) ara auth login
  7) At app.ara.so/connect, connect: Linq (iMessage), Google Calendar
  8) ara deploy personal_crm.py
  9) In the dashboard, set cron to */10 * * * * and enable it.
"""

import json
import urllib.parse
import urllib.request
from datetime import datetime, date, timezone

import ara_sdk as ara


# ---------------------------------------------------------------------------
# Supabase client — pure stdlib. Talks to PostgREST directly.
# ---------------------------------------------------------------------------
def _sb_request(method: str, path: str, body: dict | list | None = None,
                params: dict | None = None, prefer: str = "") -> list | dict:
    """
    Minimal Supabase REST helper.

    method: GET / POST / PATCH / DELETE
    path:   table path, e.g. "contacts" or "contact_notes"
    params: dict of PostgREST query params (e.g. {"name": "eq.Jane"})
    prefer: value for the Prefer header (e.g. "return=representation")
    """
    base = ara.secret("SUPABASE_URL").rstrip("/")
    key = ara.secret("SUPABASE_KEY")

    url = f"{base}/rest/v1/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params, safe="=.,")

    data = None
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    if body is not None:
        data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        if not raw:
            return []
        return json.loads(raw)


def _find_contact_id_by_name(name: str) -> str | None:
    """Case-insensitive exact-name match. Returns contact uuid or None."""
    rows = _sb_request(
        "GET",
        "contacts",
        params={"name": f"ilike.{name.strip()}", "select": "id,name", "limit": "1"},
    )
    return rows[0]["id"] if rows else None


def _fetch_contact_full(cid: str) -> dict:
    """Fetch a contact with its notes and dates embedded."""
    rows = _sb_request(
        "GET",
        "contacts",
        params={
            "id": f"eq.{cid}",
            "select": "*,contact_notes(text,source,at),contact_dates(label,date_iso,synced_to_calendar)",
        },
    )
    if not rows:
        return {}
    c = rows[0]
    # Normalize the embedded relation names so tools return consistent shapes.
    c["notes"] = sorted(c.pop("contact_notes", []) or [], key=lambda n: n["at"])
    c["dates"] = c.pop("contact_dates", []) or []
    return c


# ---------------------------------------------------------------------------
# Core CRM tools
# ---------------------------------------------------------------------------
@ara.tool
def add_contact(
    name: str,
    company: str = "",
    relationship: str = "",
    email: str = "",
    phone: str = "",
    tags: str = "",
) -> dict:
    """
    Create a new contact. If a contact with the same name already exists,
    returns the existing one instead of duplicating.

    Args:
        name: Full name of the person.
        company: Employer or affiliation.
        relationship: How you know them (colleague, friend, family, etc.).
        email: Email address.
        phone: Phone number.
        tags: Comma-separated tags.
    """
    existing = _find_contact_id_by_name(name)
    if existing:
        return {"ok": True, "id": existing, "created": False,
                "contact": _fetch_contact_full(existing)}

    payload = {
        "name": name.strip(),
        "company": company.strip(),
        "relationship": relationship.strip(),
        "email": email.strip(),
        "phone": phone.strip(),
        "tags": [t.strip() for t in tags.split(",") if t.strip()],
    }
    rows = _sb_request("POST", "contacts", body=payload,
                       prefer="return=representation")
    c = rows[0] if rows else {}
    return {"ok": True, "id": c.get("id"), "created": True, "contact": c}


@ara.tool
def append_note(name: str, note: str, source: str = "manual") -> dict:
    """
    Append a note to a contact. If the contact doesn't exist yet, creates them.

    Args:
        name: Name of the contact.
        note: The note text.
        source: Where the note came from (e.g. 'meeting', 'manual', 'email').
    """
    cid = _find_contact_id_by_name(name)
    if not cid:
        created = add_contact(name=name)
        cid = created["id"]

    _sb_request(
        "POST",
        "contact_notes",
        body={"contact_id": cid, "text": note.strip(), "source": source},
    )
    # Touch last_contact on the parent contact.
    today = datetime.now(timezone.utc).date().isoformat()
    _sb_request(
        "PATCH", "contacts",
        params={"id": f"eq.{cid}"},
        body={"last_contact": today},
    )
    return {"ok": True, "id": cid}


@ara.tool
def add_important_date(name: str, label: str, date_iso: str) -> dict:
    """
    Attach an important date to a contact (birthday, anniversary, follow-up, etc.).

    Args:
        name: Name of the contact.
        label: What the date is (e.g. 'birthday', 'anniversary', 'follow-up').
        date_iso: Date in YYYY-MM-DD format.
    """
    cid = _find_contact_id_by_name(name)
    if not cid:
        return {"ok": False, "error": f"No contact named '{name}'. Call add_contact first."}

    try:
        datetime.fromisoformat(date_iso)
    except ValueError:
        return {"ok": False, "error": f"Invalid date '{date_iso}'. Use YYYY-MM-DD."}

    rows = _sb_request(
        "POST", "contact_dates",
        body={"contact_id": cid, "label": label.strip().lower(), "date_iso": date_iso},
        prefer="return=representation",
    )
    return {"ok": True, "id": cid, "date": rows[0] if rows else None}


@ara.tool
def lookup_contact(name: str) -> dict:
    """Return everything stored about a contact by name."""
    cid = _find_contact_id_by_name(name)
    if not cid:
        return {"ok": False, "error": f"No contact named '{name}'."}
    return {"ok": True, "contact": _fetch_contact_full(cid)}


@ara.tool
def list_contacts(tag: str = "") -> dict:
    """
    List all contacts, optionally filtered by a tag.

    Args:
        tag: If provided, only return contacts with this tag.
    """
    params = {"select": "name,company,last_contact,tags", "order": "name"}
    if tag:
        # PostgREST array contains: tags=cs.{<value>}
        params["tags"] = f"cs.{{{tag.strip()}}}"
    rows = _sb_request("GET", "contacts", params=params)
    summary = [{"name": r["name"], "company": r.get("company"),
                "last_contact": r.get("last_contact")} for r in rows]
    return {"ok": True, "count": len(summary), "contacts": summary}


@ara.tool
def upcoming_dates(days_ahead: int = 30) -> dict:
    """
    Return important dates occurring in the next N days (birthdays recur annually).

    Args:
        days_ahead: How many days to look ahead.
    """
    rows = _sb_request(
        "GET", "contact_dates",
        params={"select": "label,date_iso,contacts(name)"},
    )
    today = date.today()
    hits = []
    for d in rows:
        try:
            orig = date.fromisoformat(d["date_iso"])
        except (ValueError, TypeError):
            continue
        if d["label"] in ("birthday", "anniversary"):
            this_year = orig.replace(year=today.year)
            next_occ = this_year if this_year >= today else orig.replace(year=today.year + 1)
        else:
            next_occ = orig
        delta = (next_occ - today).days
        if 0 <= delta <= days_ahead:
            hits.append({
                "name": (d.get("contacts") or {}).get("name"),
                "label": d["label"],
                "date": next_occ.isoformat(),
                "days_away": delta,
            })
    hits.sort(key=lambda x: x["days_away"])
    return {"ok": True, "count": len(hits), "upcoming": hits}


@ara.tool
def stale_contacts(days: int = 60) -> dict:
    """
    Return contacts you haven't talked to in `days` days — good for a nudge list.
    """
    rows = _sb_request(
        "GET", "contacts",
        params={"select": "name,last_contact"},
    )
    today = date.today()
    stale = []
    for c in rows:
        last = c.get("last_contact")
        if not last:
            stale.append({"name": c["name"], "last_contact": None, "days_since": None})
            continue
        days_since = (today - date.fromisoformat(last)).days
        if days_since >= days:
            stale.append({"name": c["name"], "last_contact": last, "days_since": days_since})
    stale.sort(key=lambda x: x["days_since"] or 99999, reverse=True)
    return {"ok": True, "count": len(stale), "stale": stale}


@ara.tool
def pending_calendar_syncs() -> dict:
    """
    Return all (contact, date) pairs that haven't been synced to the calendar yet.
    The agent should iterate these and call the Google Calendar connector tool
    (e.g. google_calendar_create_event) to add each one, then call
    mark_dates_synced to record success.
    """
    rows = _sb_request(
        "GET", "contact_dates",
        params={
            "select": "label,date_iso,contacts(name)",
            "synced_to_calendar": "eq.false",
        },
    )
    pending = [{
        "name": (d.get("contacts") or {}).get("name"),
        "label": d["label"],
        "date": d["date_iso"],
        "recurring": d["label"] in ("birthday", "anniversary"),
    } for d in rows]
    return {"ok": True, "count": len(pending), "pending": pending}


@ara.tool
def mark_dates_synced(name: str, label: str) -> dict:
    """
    Mark a contact's date as synced to calendar so we don't re-add it.
    Call AFTER successfully creating a calendar event.
    """
    cid = _find_contact_id_by_name(name)
    if not cid:
        return {"ok": False, "error": f"No contact named '{name}'."}
    rows = _sb_request(
        "PATCH", "contact_dates",
        params={"contact_id": f"eq.{cid}", "label": f"eq.{label.strip().lower()}"},
        body={"synced_to_calendar": True},
        prefer="return=representation",
    )
    return {"ok": True, "marked": len(rows)}


@ara.tool
def lookup_contacts_bulk(names: str) -> dict:
    """
    Look up multiple contacts at once. Returns CRM data for every match so you
    can assemble a meeting prep brief with notes for each attendee.

    Args:
        names: Comma-separated list of names (e.g. "Jane Smith, Mike Chen").
    """
    wanted = [n.strip() for n in names.split(",") if n.strip()]
    if not wanted:
        return {"ok": True, "found": {}, "missing": []}

    # Build an OR filter: name=in.("Jane Smith","Mike Chen")
    quoted = ",".join(f'"{n}"' for n in wanted)
    rows = _sb_request(
        "GET", "contacts",
        params={
            "select": "name,company,relationship,tags,last_contact,"
                      "contact_notes(text,at),contact_dates(label,date_iso)",
            "name": f"in.({quoted})",
        },
    )

    found = {}
    for c in rows:
        notes = sorted(c.get("contact_notes") or [], key=lambda n: n["at"])
        found[c["name"]] = {
            "company": c.get("company", ""),
            "relationship": c.get("relationship", ""),
            "tags": c.get("tags") or [],
            "last_contact": c.get("last_contact"),
            "recent_notes": [n["text"] for n in notes[-3:][::-1]],
            "dates": c.get("contact_dates") or [],
        }
    missing = [n for n in wanted if n not in found]
    return {"ok": True, "found": found, "missing": missing}


@ara.tool
def get_reminded_meetings() -> dict:
    """
    Return the set of calendar meeting IDs we have already texted a reminder
    for. Use this to avoid sending duplicate reminders for the same meeting.
    """
    rows = _sb_request(
        "GET", "reminded_meetings",
        params={"select": "meeting_id", "order": "at.desc", "limit": "1000"},
    )
    return {"ok": True, "reminded_ids": [r["meeting_id"] for r in rows]}


@ara.tool
def mark_meeting_reminded(meeting_id: str, meeting_title: str) -> dict:
    """
    Record that we've texted a reminder for this meeting. Call this AFTER
    successfully sending the reminder via linq_send_message.

    Args:
        meeting_id: The calendar event's unique ID.
        meeting_title: The event title (for logging).
    """
    # Upsert via Prefer: resolution=merge-duplicates (meeting_id is PK)
    _sb_request(
        "POST", "reminded_meetings",
        body={"meeting_id": meeting_id, "title": meeting_title},
        prefer="resolution=merge-duplicates",
    )
    return {"ok": True, "logged": meeting_id}


# ---------------------------------------------------------------------------
# Automation — the brain.
# ---------------------------------------------------------------------------
SYSTEM_INSTRUCTIONS = """
You are Nick's personal CRM agent. You help him remember the people he knows.

ROUTING — figure out the user's intent and act:

1. INGESTING MEETING NOTES. If the user sends raw meeting notes or transcript text:
   - Extract every person mentioned (full name if possible).
   - For each person, call append_note with a concise 1-2 sentence summary of
     what was discussed, source='meeting'.
   - If the person is new, append_note auto-creates them; afterwards call
     add_contact again with company/relationship/email/tags if the notes reveal them.
   - Extract any important dates mentioned (birthdays, follow-ups, deadlines)
     and call add_important_date for each.

2. LOOKING UP A PERSON. If the user asks "what do I know about X" or similar,
   call lookup_contact and respond with a concise brief: company, relationship,
   last few notes, upcoming dates.

3. SCHEDULED RUN (no user message — the run was triggered by a cron tick).
   On EVERY scheduled run, always do step A. Only do step B if it's the
   morning and you haven't briefed today yet.

   A) MEETING REMINDERS — always:
      1. Use the Google Calendar connector to list events starting in the
         next 30 minutes from the user's primary calendar.
      2. Call get_reminded_meetings to get the set of meeting IDs you've
         already texted about. Skip any event already in that set.
      3. For each NEW upcoming meeting:
         a. Extract attendee names (exclude the user themselves).
         b. Call lookup_contacts_bulk with those names (comma-separated).
         c. Compose a concise text in this format:
              📅 In 15 min: <Meeting title> at <time>
              With: <attendees>

              <For each attendee found in CRM:>
              • <Name> (<company>, <relationship>)
                — <most recent note, 1 line>
                — Last talked: <last_contact>

              <If any attendees are missing from CRM:>
              New faces: <names>
         d. Send via linq_send_message.
         e. Call mark_meeting_reminded(meeting_id, meeting_title).

   B) DAILY BRIEFING — only if current local time is between 09:00 and 09:10
      AND no briefing has been sent today:
      1. Call upcoming_dates(days_ahead=7).
      2. Call stale_contacts(days=60).
      3. Call pending_calendar_syncs — sync any items to Google Calendar
         (see rule 4) before composing the briefing.
      4. Compose in this format:
           Good morning! Here's today:
           🎂 Birthdays this week: <names + days away> (or "none")
           📅 Follow-ups due: <names + dates> (or "none")
           👋 Consider reaching out to: <1-3 stale contacts>
      5. Send via linq_send_message.

   If neither A nor B has anything to do, reply with a short "nothing to
   report" and do NOT send any text — don't spam the user on quiet cron
   ticks.

4. CALENDAR SYNC. If the user says "sync to calendar" or during daily briefing:
   - Call pending_calendar_syncs.
   - For each pending item, use the Google Calendar connector tool to create
     an event. For birthdays/anniversaries, make it recurring yearly. Set
     event title like "🎂 Jane Smith's birthday".
   - After each successful calendar create, call mark_dates_synced.

5. ADDING A CONTACT. If the user says "add [person]" or "remember [person]",
   call add_contact with whatever details they gave.

Be concise. Don't ask questions unless truly ambiguous — just do the work and
report what you did. When summarizing, prefer bullet points.
""".strip()


app = ara.Automation(
    "personal-crm",
    system_instructions=SYSTEM_INSTRUCTIONS,
    tools=[
        add_contact,
        append_note,
        add_important_date,
        lookup_contact,
        lookup_contacts_bulk,
        list_contacts,
        upcoming_dates,
        stale_contacts,
        pending_calendar_syncs,
        mark_dates_synced,
        get_reminded_meetings,
        mark_meeting_reminded,
    ],
)
