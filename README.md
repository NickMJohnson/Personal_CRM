# Personal CRM — Ara Agent

A personal CRM agent built on [Ara](https://ara.so) for the Ara hackathon. It
keeps track of the people you know, their notes, and important dates — fed
automatically from [Granola](https://www.granola.ai) meeting notes via Zapier,
and kept in sync with your Google Calendar.

## Features

- **Automatic Granola ingestion** — every meeting you take in Granola is
  POSTed to the agent via Zapier; it extracts attendees, notes, and dates
  without you lifting a finger.
- **Ad-hoc note ingestion** — paste raw notes over iMessage and the agent
  does the same extraction.
- **Contact lookup** — ask "what do I know about Jane?" and get a concise brief.
- **Birthday & follow-up sync** — important dates become recurring Google
  Calendar events.
- **Meeting reminders over iMessage** — texts you ~15 min before each meeting
  with CRM context on the attendees.
- **Daily briefing (9am)** — three-section morning text:
  - 🎂 birthdays this week
  - 📅 upcoming events (next 30 days: anniversaries, follow-ups, deadlines)
  - 👋 recommended people to reach out to, each with a one-line
    "about what" suggestion pulled from their most recent note
- **Midday & afternoon nudges (1pm, 5pm)** — quick reminder text if any
  birthday or important event is happening today, so nothing slips.

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

## Granola → Zapier integration

Granola's [Zapier integration](https://docs.granola.ai/help-center/sharing/integrations/zapier)
lets Zapier fire a webhook every time a meeting note is created. Wire that
webhook to this agent and every Granola meeting flows straight into the CRM.

**Requires:** Granola paid plan (Zapier integration is paid-only) and a Zapier
account.

### 1. Expose the agent as an HTTP endpoint

`ara deploy` returns an app run URL + a runtime key. Keep both:

```bash
ara deploy personal_crm.py
# → app id, run URL (POST), and runtime key
```

The run URL accepts `POST` with a JSON body and an `Authorization: Bearer
<runtime_key>` header. The body becomes the automation's input payload — the
agent reads it the same way as an iMessage.

### 2. Create the Zap

In Zapier, create a new Zap:

**Trigger** — *Granola → Note Added to Folder*
- Connect your Granola account
- Pick the folder you want auto-ingested (e.g. create one called `CRM`)
- Any note you drop in that folder will fire the Zap

**Action** — *Webhooks by Zapier → POST*
- URL: your Ara run URL from step 1
- Payload type: `JSON`
- Data:
  ```json
  {
    "input": "Meeting: {{title}}\nDate: {{created_at}}\nAttendees: {{attendees}}\n\n{{transcript}}"
  }
  ```
  (Use Zapier's field picker to pull `title`, `transcript`, etc. from the
  Granola event.)
- Headers:
  ```
  Authorization: Bearer <your ara runtime key>
  Content-Type: application/json
  ```

### 3. Test it

Drop a note into your `CRM` Granola folder. Within a few seconds:

- The Zap runs and POSTs to Ara
- The agent parses attendees, summarizes the meeting, and calls
  `append_note` / `add_contact` / `add_important_date` as needed
- Any birthday or follow-up dates get synced to Google Calendar on the next
  cron tick

### Alternative triggers

Granola's Zapier app also exposes a **"Note Shared to Zapier"** trigger —
manual share-button instead of folder-based. Use that if you'd rather
hand-pick which meetings reach the CRM.

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
