# Personal CRM — Ara Agent

A personal CRM agent built on [Ara](https://ara.so) for the Ara hackathon. It
keeps track of the people you know, their notes, and important dates — fed by
meeting notes over iMessage and kept in sync with your Google Calendar.

## Features

- **Meeting note ingestion** — paste raw notes and the agent extracts people,
  notes, and dates automatically.
- **Contact lookup** — ask "what do I know about Jane?" and get a concise brief.
- **Birthday & follow-up sync** — important dates become recurring Google
  Calendar events.
- **Meeting reminders over iMessage** — texts you ~15 min before each meeting
  with CRM context on the attendees.
- **Daily briefing** — morning text with upcoming birthdays, follow-ups due,
  and people you've been out of touch with.

## Architecture

- `personal_crm.py` — Ara agent with 12 tools for CRM CRUD + briefings.
- `schema.sql` — Supabase (Postgres) schema: contacts, notes, dates,
  reminded meetings.

Storage goes through Supabase's PostgREST API using Python's stdlib
(`urllib.request`), so no extra pip deps beyond `ara-sdk`.

## Setup

1. Create a free Supabase project at https://supabase.com
2. Run `schema.sql` in the Supabase SQL editor
3. Grab your Project URL and service_role key from Supabase → Settings → API
4. Set them as Ara secrets: `SUPABASE_URL` and `SUPABASE_KEY`
5. At [app.ara.so/connect](https://app.ara.so/connect), connect Linq (iMessage)
   and Google Calendar
6. Deploy:
   ```bash
   pip install ara-sdk
   ara auth login
   ara deploy personal_crm.py
   ```
7. In the app.ara.so dashboard, set the cron to `*/10 * * * *` and enable it

## Tools exposed to the agent

| Tool | Purpose |
|---|---|
| `add_contact` | Create a new contact (idempotent by name) |
| `append_note` | Add a dated note to a contact |
| `add_important_date` | Attach a birthday / anniversary / follow-up |
| `lookup_contact` | Full details for one person |
| `lookup_contacts_bulk` | Bulk lookup — used for meeting reminders |
| `list_contacts` | List everyone, optionally tag-filtered |
| `upcoming_dates` | Birthdays/events in the next N days |
| `stale_contacts` | People you haven't talked to in a while |
| `pending_calendar_syncs` | Dates not yet on your calendar |
| `mark_dates_synced` | Mark a date as synced after calendar insert |
| `get_reminded_meetings` | Dedup list of meeting IDs already texted about |
| `mark_meeting_reminded` | Record a meeting reminder was sent |

Plus Ara's built-in connectors (`linq_send_message`, `google_calendar_*`) for
the outbound texts and calendar writes.
