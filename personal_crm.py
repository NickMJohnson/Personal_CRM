"""
Personal CRM built on Ara — Supabase-backed.

Keeps track of people you know, notes about them, and important dates.
You can feed it meeting notes (via iMessage / API call) and it will
extract contacts, notes, and dates, then sync birthdays/follow-ups to
your calendar via the Google Calendar connector. On scheduled runs it
sends iMessage reminders for upcoming meetings with CRM notes on the
attendees.

Each tool is SELF-CONTAINED: Ara's runtime ships each tool's source
independently, so module-level helpers are not visible inside tool
execution. The Supabase client is inlined as a nested function in every
tool that needs it.

HOW TO RUN:
  1) Create a Supabase project at https://supabase.com (free tier).
  2) Open the SQL editor, paste schema.sql, and run it.
  3) In Supabase → Settings → API, grab:
       - Project URL              -> SUPABASE_URL
       - service_role secret key  -> SUPABASE_KEY
  4) Put both in .env at the project root (ara deploy picks them up and
     uploads them as runtime secrets).
  5) pip install ara-sdk
  6) ara auth login
  7) At app.ara.so/connect, connect: Linq (iMessage), Google Calendar
  8) ara deploy personal_crm.py
  9) In the dashboard, set cron to */10 * * * * and enable it.
"""

import ara_sdk as ara


# ---------------------------------------------------------------------------
# Diagnostic
# ---------------------------------------------------------------------------
@ara.tool
def debug_supabase_connect() -> dict:
    """Report whether SUPABASE_URL/SUPABASE_KEY are present and whether a
    basic GET against the contacts table succeeds. For debugging only."""
    import os, json, urllib.parse, urllib.request
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return {"ok": False, "url_present": bool(url), "key_present": bool(key),
                "error": "Missing SUPABASE_URL or SUPABASE_KEY in runtime env."}
    try:
        req = urllib.request.Request(
            f"{url.rstrip('/')}/rest/v1/contacts?select=id&limit=1",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode()
        return {"ok": True, "url_present": True, "key_present": True,
                "sample": body[:120]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "url_present": True, "key_present": True,
                "error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# Core CRM tools — each one is self-contained
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
    import os, json, urllib.parse, urllib.request

    def sb(method, path, body=None, params=None, prefer=""):
        base = os.environ["SUPABASE_URL"].rstrip("/")
        key = os.environ["SUPABASE_KEY"]
        url = f"{base}/rest/v1/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, safe="=.,")
        headers = {"apikey": key, "Authorization": f"Bearer {key}",
                   "Content-Type": "application/json"}
        if prefer:
            headers["Prefer"] = prefer
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else []

    existing = sb("GET", "contacts",
                  params={"name": f"ilike.{name.strip()}", "select": "id,name", "limit": "1"})
    if existing:
        return {"ok": True, "id": existing[0]["id"], "created": False}

    payload = {
        "name": name.strip(),
        "company": company.strip(),
        "relationship": relationship.strip(),
        "email": email.strip(),
        "phone": phone.strip(),
        "tags": [t.strip() for t in tags.split(",") if t.strip()],
    }
    rows = sb("POST", "contacts", body=payload, prefer="return=representation")
    c = rows[0] if rows else {}
    return {"ok": True, "id": c.get("id"), "created": True, "contact": c}


@ara.tool
def append_note(name: str, note: str, source: str = "manual") -> dict:
    """
    Append a note to a contact. If the contact doesn't exist yet, creates them.

    Args:
        name: Name of the contact.
        note: The note text.
        source: Where the note came from ('meeting', 'granola', 'manual', 'email').
    """
    import os, json, urllib.parse, urllib.request
    from datetime import datetime, timezone

    def sb(method, path, body=None, params=None, prefer=""):
        base = os.environ["SUPABASE_URL"].rstrip("/")
        key = os.environ["SUPABASE_KEY"]
        url = f"{base}/rest/v1/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, safe="=.,")
        headers = {"apikey": key, "Authorization": f"Bearer {key}",
                   "Content-Type": "application/json"}
        if prefer:
            headers["Prefer"] = prefer
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else []

    found = sb("GET", "contacts",
               params={"name": f"ilike.{name.strip()}", "select": "id", "limit": "1"})
    if found:
        cid = found[0]["id"]
    else:
        created = sb("POST", "contacts", body={"name": name.strip(), "tags": []},
                     prefer="return=representation")
        cid = created[0]["id"]

    sb("POST", "contact_notes",
       body={"contact_id": cid, "text": note.strip(), "source": source})
    today = datetime.now(timezone.utc).date().isoformat()
    sb("PATCH", "contacts", params={"id": f"eq.{cid}"}, body={"last_contact": today})
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
    import os, json, urllib.parse, urllib.request
    from datetime import datetime

    def sb(method, path, body=None, params=None, prefer=""):
        base = os.environ["SUPABASE_URL"].rstrip("/")
        key = os.environ["SUPABASE_KEY"]
        url = f"{base}/rest/v1/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, safe="=.,")
        headers = {"apikey": key, "Authorization": f"Bearer {key}",
                   "Content-Type": "application/json"}
        if prefer:
            headers["Prefer"] = prefer
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else []

    try:
        datetime.fromisoformat(date_iso)
    except ValueError:
        return {"ok": False, "error": f"Invalid date '{date_iso}'. Use YYYY-MM-DD."}

    found = sb("GET", "contacts",
               params={"name": f"ilike.{name.strip()}", "select": "id", "limit": "1"})
    if not found:
        return {"ok": False, "error": f"No contact named '{name}'. Call add_contact first."}
    cid = found[0]["id"]

    rows = sb("POST", "contact_dates",
              body={"contact_id": cid, "label": label.strip().lower(), "date_iso": date_iso},
              prefer="return=representation")
    return {"ok": True, "id": cid, "date": rows[0] if rows else None}


@ara.tool
def lookup_contact(name: str) -> dict:
    """Return everything stored about a contact by name."""
    import os, json, urllib.parse, urllib.request

    def sb(method, path, body=None, params=None, prefer=""):
        base = os.environ["SUPABASE_URL"].rstrip("/")
        key = os.environ["SUPABASE_KEY"]
        url = f"{base}/rest/v1/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, safe="=.,")
        headers = {"apikey": key, "Authorization": f"Bearer {key}",
                   "Content-Type": "application/json"}
        if prefer:
            headers["Prefer"] = prefer
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else []

    rows = sb("GET", "contacts", params={
        "name": f"ilike.{name.strip()}",
        "select": "*,contact_notes(text,source,at),contact_dates(label,date_iso,synced_to_calendar)",
        "limit": "1",
    })
    if not rows:
        return {"ok": False, "error": f"No contact named '{name}'."}
    c = rows[0]
    c["notes"] = sorted(c.pop("contact_notes", []) or [], key=lambda n: n["at"])
    c["dates"] = c.pop("contact_dates", []) or []
    return {"ok": True, "contact": c}


@ara.tool
def list_contacts(tag: str = "") -> dict:
    """
    List all contacts, optionally filtered by a tag.

    Args:
        tag: If provided, only return contacts with this tag.
    """
    import os, json, urllib.parse, urllib.request

    def sb(method, path, body=None, params=None, prefer=""):
        base = os.environ["SUPABASE_URL"].rstrip("/")
        key = os.environ["SUPABASE_KEY"]
        url = f"{base}/rest/v1/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, safe="=.,")
        headers = {"apikey": key, "Authorization": f"Bearer {key}",
                   "Content-Type": "application/json"}
        if prefer:
            headers["Prefer"] = prefer
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else []

    params = {"select": "name,company,last_contact,tags", "order": "name"}
    if tag:
        params["tags"] = f"cs.{{{tag.strip()}}}"
    rows = sb("GET", "contacts", params=params)
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
    import os, json, urllib.parse, urllib.request
    from datetime import date

    def sb(method, path, body=None, params=None, prefer=""):
        base = os.environ["SUPABASE_URL"].rstrip("/")
        key = os.environ["SUPABASE_KEY"]
        url = f"{base}/rest/v1/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, safe="=.,")
        headers = {"apikey": key, "Authorization": f"Bearer {key}",
                   "Content-Type": "application/json"}
        if prefer:
            headers["Prefer"] = prefer
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else []

    rows = sb("GET", "contact_dates", params={"select": "label,date_iso,contacts(name)"})
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
    Return contacts you haven't talked to in `days` days — includes the most
    recent note on each so the agent can recommend WHAT to follow up about.
    """
    import os, json, urllib.parse, urllib.request
    from datetime import date

    def sb(method, path, body=None, params=None, prefer=""):
        base = os.environ["SUPABASE_URL"].rstrip("/")
        key = os.environ["SUPABASE_KEY"]
        url = f"{base}/rest/v1/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, safe="=.,")
        headers = {"apikey": key, "Authorization": f"Bearer {key}",
                   "Content-Type": "application/json"}
        if prefer:
            headers["Prefer"] = prefer
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else []

    rows = sb("GET", "contacts", params={
        "select": "name,last_contact,relationship,contact_notes(text,at)",
    })
    today = date.today()
    stale = []
    for c in rows:
        notes = sorted(c.get("contact_notes") or [], key=lambda n: n["at"], reverse=True)
        last_note = notes[0]["text"][:200] if notes else None
        last = c.get("last_contact")
        base_row = {
            "name": c["name"],
            "relationship": c.get("relationship") or "",
            "last_note": last_note,
        }
        if not last:
            stale.append({**base_row, "last_contact": None, "days_since": None})
            continue
        days_since = (today - date.fromisoformat(last)).days
        if days_since >= days:
            stale.append({**base_row, "last_contact": last, "days_since": days_since})
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
    import os, json, urllib.parse, urllib.request

    def sb(method, path, body=None, params=None, prefer=""):
        base = os.environ["SUPABASE_URL"].rstrip("/")
        key = os.environ["SUPABASE_KEY"]
        url = f"{base}/rest/v1/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, safe="=.,")
        headers = {"apikey": key, "Authorization": f"Bearer {key}",
                   "Content-Type": "application/json"}
        if prefer:
            headers["Prefer"] = prefer
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else []

    rows = sb("GET", "contact_dates", params={
        "select": "label,date_iso,contacts(name)",
        "synced_to_calendar": "eq.false",
    })
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
    import os, json, urllib.parse, urllib.request

    def sb(method, path, body=None, params=None, prefer=""):
        base = os.environ["SUPABASE_URL"].rstrip("/")
        key = os.environ["SUPABASE_KEY"]
        url = f"{base}/rest/v1/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, safe="=.,")
        headers = {"apikey": key, "Authorization": f"Bearer {key}",
                   "Content-Type": "application/json"}
        if prefer:
            headers["Prefer"] = prefer
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else []

    found = sb("GET", "contacts",
               params={"name": f"ilike.{name.strip()}", "select": "id", "limit": "1"})
    if not found:
        return {"ok": False, "error": f"No contact named '{name}'."}
    cid = found[0]["id"]
    rows = sb("PATCH", "contact_dates",
              params={"contact_id": f"eq.{cid}", "label": f"eq.{label.strip().lower()}"},
              body={"synced_to_calendar": True},
              prefer="return=representation")
    return {"ok": True, "marked": len(rows)}


@ara.tool
def lookup_contacts_bulk(names: str) -> dict:
    """
    Look up multiple contacts at once. Returns CRM data for every match so you
    can assemble a meeting prep brief with notes for each attendee.

    Args:
        names: Comma-separated list of names (e.g. "Jane Smith, Mike Chen").
    """
    import os, json, urllib.parse, urllib.request

    def sb(method, path, body=None, params=None, prefer=""):
        base = os.environ["SUPABASE_URL"].rstrip("/")
        key = os.environ["SUPABASE_KEY"]
        url = f"{base}/rest/v1/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, safe="=.,")
        headers = {"apikey": key, "Authorization": f"Bearer {key}",
                   "Content-Type": "application/json"}
        if prefer:
            headers["Prefer"] = prefer
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else []

    wanted = [n.strip() for n in names.split(",") if n.strip()]
    if not wanted:
        return {"ok": True, "found": {}, "missing": []}

    quoted = ",".join(f'"{n}"' for n in wanted)
    rows = sb("GET", "contacts", params={
        "select": "name,company,relationship,tags,last_contact,"
                  "contact_notes(text,at),contact_dates(label,date_iso)",
        "name": f"in.({quoted})",
    })

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
    import os, json, urllib.parse, urllib.request

    def sb(method, path, body=None, params=None, prefer=""):
        base = os.environ["SUPABASE_URL"].rstrip("/")
        key = os.environ["SUPABASE_KEY"]
        url = f"{base}/rest/v1/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, safe="=.,")
        headers = {"apikey": key, "Authorization": f"Bearer {key}",
                   "Content-Type": "application/json"}
        if prefer:
            headers["Prefer"] = prefer
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else []

    rows = sb("GET", "reminded_meetings",
              params={"select": "meeting_id", "order": "at.desc", "limit": "1000"})
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
    import os, json, urllib.parse, urllib.request

    def sb(method, path, body=None, params=None, prefer=""):
        base = os.environ["SUPABASE_URL"].rstrip("/")
        key = os.environ["SUPABASE_KEY"]
        url = f"{base}/rest/v1/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, safe="=.,")
        headers = {"apikey": key, "Authorization": f"Bearer {key}",
                   "Content-Type": "application/json"}
        if prefer:
            headers["Prefer"] = prefer
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else []

    sb("POST", "reminded_meetings",
       body={"meeting_id": meeting_id, "title": meeting_title},
       prefer="resolution=merge-duplicates")
    return {"ok": True, "logged": meeting_id}


# ---------------------------------------------------------------------------
# Automation — the brain.
# ---------------------------------------------------------------------------
SYSTEM_INSTRUCTIONS = """
You are Nick's personal CRM agent. You help him remember the people he knows.

CRITICAL — DATA SOURCE.
The CRM lives in Supabase and is accessed ONLY through these custom tools:
  add_contact, append_note, add_important_date, lookup_contact,
  lookup_contacts_bulk, list_contacts, upcoming_dates, stale_contacts,
  pending_calendar_syncs, mark_dates_synced, get_reminded_meetings,
  mark_meeting_reminded.
NEVER use Affinity, HubSpot, Salesforce, Pipedrive, Notion, Airtable, or any
other CRM-like connector for storing or retrieving contacts — those are NOT
where this CRM lives. If a tool outside the list above looks relevant to
contact storage, ignore it. The ONLY connector tools you may call are
linq_send_message (outbound iMessage) and google_calendar_* (calendar
read/write).

ROUTING — figure out the user's intent and act:

1. INGESTING MEETING NOTES. If the user sends raw meeting notes or transcript
   text (via iMessage OR a Granola note arriving through the Zapier webhook):

   A) IDENTIFY THE PEOPLE.
      Before writing anything, call list_contacts to see who already exists.
      For each person mentioned in the notes, decide confidence:

      - HIGH CONFIDENCE (commit): full name matches an existing contact, OR a
        first name with exactly one possible match in the CRM, OR a new name
        accompanied by clarifying context (company, role, relationship).
      - LOW CONFIDENCE (do NOT commit, ask instead): a first name with
        multiple possible matches, a last-name-only reference, or a name with
        no surrounding context at all.

      If there are any LOW CONFIDENCE names, STOP ingestion for those names
      and send ONE disambiguation question via linq_send_message listing every
      ambiguity at once:
        ❓ Quick check before I save today's meeting notes:
        • 'Mike' — Mike Chen (Anthropic) or Mike Park (cousin)?
        • 'Sarah' — no Sarah in CRM yet. Add as new contact?
      Then wait — the user's next reply resumes ingestion with their answers.
      Commit the HIGH-CONFIDENCE attendees in the meantime.

   B) RELEVANCE FILTER — what to save.
      Summarize only durable, decision-relevant signal. One to three short
      bullet-style notes per attendee is the target — NOT a transcript.
      SAVE:
        • Role, company, what they're working on
        • How you know them, mutual connections, family context
        • Commitments either side made ("I'll intro him to Sam next week")
        • Preferences, interests, hobbies
        • Life events (new job, baby, move, illness, travel)
        • Important dates (birthday, anniversary, follow-up deadline)
      DROP:
        • Meeting logistics (time, place, who was late)
        • Small talk that reveals nothing durable about the person
        • Generic observations without personal signal
        • Your own opinions about how the meeting went

   C) WRITE.
      For each HIGH-CONFIDENCE person:
        - Call append_note with your filtered 1-2 sentence summary,
          source='meeting' (or 'granola' if the note came via Zapier).
        - If they were newly created by append_note, call add_contact again
          with any company / relationship / email / tags the notes clearly
          reveal. Skip fields the notes don't cover — don't guess.
        - For any important dates mentioned (birthday, follow-up, deadline),
          call add_important_date.

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
      1. Call upcoming_dates(days_ahead=30).
      2. Call stale_contacts(days=45).
      3. Call pending_calendar_syncs — sync any items to Google Calendar
         (see rule 4) before composing the briefing.
      4. From upcoming_dates, split the results:
           - BIRTHDAYS_THIS_WEEK = items where label == 'birthday' and days_away <= 7
           - UPCOMING_EVENTS = all other items (anniversaries, follow-ups,
             deadlines, etc.), across the full 30-day window.
      5. From stale_contacts, pick the top 3 most overdue where last_note is
         non-empty. For each, derive a short "about what" suggestion from the
         last_note (e.g. last_note "discussed her job search at Anthropic" →
         "check in on Anthropic job search").
      6. Compose in this format (skip any section that is empty):
           Good morning! Your CRM briefing:

           🎂 Birthdays this week:
           • <Name> — in <N> days (<date>)

           📅 Upcoming events:
           • <label> — <Name> — <date> (in <N> days)

           👋 Reach out to:
           • <Name> (<relationship>, last talked <N> days ago)
             └ about: <short suggestion derived from last_note>
      7. Send via linq_send_message.

   C) MIDDAY / AFTERNOON NUDGE — if current local time is between 13:00–13:10
      OR 17:00–17:10, and a nudge hasn't already been sent in this window:
      1. Call upcoming_dates(days_ahead=1).
      2. Keep only items where days_away == 0 (happening today).
      3. If none, do nothing.
      4. If any, compose a short text and send via linq_send_message:
           🔔 Today's reminders:
           • 🎂 <Name>'s birthday
           • 📅 <label> — <Name>

   If none of A, B, or C have anything to do, reply with a short "nothing to
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
    required_env=["SUPABASE_URL", "SUPABASE_KEY"],
    tools=[
        debug_supabase_connect,
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
