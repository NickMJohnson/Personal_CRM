# Personal CRM — Ara Agent

> The friend who remembers everyone you've ever met — so you don't have to.

A personal CRM agent built on [Ara](https://ara.so), powered by Supabase,
Google Calendar, and [Granola](https://granola.ai). It ingests your meeting
notes automatically, keeps a running dossier on the people you know, reminds
you about birthdays and follow-ups, and texts you attendee context before
every meeting — all over iMessage.

Built for the Ara hackathon (April 2026).

---

## Why this exists

Remembering people is expensive. The details you need — Sarah is job
searching, Mike's kid just turned two, you promised Ben an intro last month —
get dropped between meetings. Tools like Granola capture notes; CRMs store
them; calendars schedule around them. Nothing joins them up.

This agent does.

---

## What it does

- 🎙 **Captures automatically.** Every meeting you take in Granola flows
  through Zapier into Supabase as structured contact notes, with attendees
  extracted and the relevant details filtered in.
- 🧠 **Remembers selectively.** Only durable, decision-useful signal is
  saved — who someone is, what they're working on, commitments you made.
  Meeting logistics and small talk get dropped.
- 🗣 **Asks when unsure.** When a first name is ambiguous ("Mike"?) or
  context is missing, the agent texts you a single disambiguation question
  before writing anything to the database.
- 📅 **Writes to your calendar.** Birthdays, anniversaries, and follow-up
  deadlines become recurring Google Calendar events, synced automatically.
- 📱 **Texts you the right thing at the right time.**
  - **~15 min before a meeting:** a dossier on the attendees
    (company, relationship, most recent note, when you last talked).
  - **9am daily briefing:** three sections —
    🎂 birthdays this week, 📅 upcoming events in the next 30 days,
    👋 recommended people to reach out to with a one-line *about-what*
    suggestion pulled from their most recent note.
  - **1pm & 5pm nudges:** a quick text if any birthday or event is happening
    *today*, so nothing slips.
- 🔍 **Answers on demand.** Text *"what do I know about Jane?"* and get a
  concise brief back in seconds.

---

## Architecture

```
       ┌────────────────┐
       │    Granola     │   (you take a meeting — note auto-saved)
       └────────┬───────┘
                │  note added to "CRM" folder
                ▼
       ┌────────────────┐
       │     Zapier     │   (Webhooks by Zapier — POST with JSON)
       └────────┬───────┘
                │  HTTPS + Bearer <runtime_key>
                ▼
       ┌────────────────────────────┐       ┌────────────────┐
       │       Ara Automation       │◀──────│   cron (*/10)  │
       │        personal_crm        │◀──────│  Linq iMessage │
       └──┬─────────┬──────────┬────┘       └────────────────┘
          │         │          │
          ▼         ▼          ▼
   ┌──────────┐ ┌────────┐ ┌─────────────┐
   │ Supabase │ │ Google │ │ Linq / SMS  │
   │ Postgres │ │ Cal.   │ │ (iMessage)  │
   └──────────┘ └────────┘ └─────────────┘
```

**Stateless Python, persistent data in Supabase.** The Ara automation is a
single Python file that declares 12 tools and a system prompt — Ara handles
scheduling, auth, and the LLM turn. Storage goes through Supabase's PostgREST
API using only Python's stdlib (`urllib.request`), so the agent has no
dependencies beyond `ara-sdk`.

---

## How the agent thinks

A single system prompt routes every invocation. The agent figures out intent
from the trigger type:

| Trigger | What the agent does |
|---|---|
| **Raw meeting notes** (iMessage paste OR Granola-via-Zapier payload) | Identifies attendees → asks about any ambiguous names → summarizes durable signal → writes contacts, notes, and important dates. |
| **User question** ("what do I know about Jane?") | Reads the full dossier and replies with a concise brief. |
| **Cron tick — always** | Checks Google Calendar for meetings in the next 30 min. For any not-yet-reminded meeting, composes an attendee brief and texts it, then records the reminder to avoid duplicates. |
| **Cron tick @ 09:00** | Runs the daily briefing: birthdays this week, upcoming events (30 days), top 3 reach-outs with *about-what* suggestions. Syncs pending dates to Google Calendar. |
| **Cron tick @ 13:00 / 17:00** | Sends a short nudge for anything scheduled today. |
| **"sync to calendar" / "add [person]"** | Direct tool calls. |

The agent is terse by design — it acts, then reports. It only asks when
truly ambiguous (e.g. an unfamiliar first name with multiple possible
matches), and asks once per batch so it isn't pinging you all day.

---

## Setup

### 1. Supabase

```bash
# In Supabase SQL editor, run:
schema.sql
```

Grab from Supabase → Settings → API:
- **Project URL** → goes into `SUPABASE_URL`
- **service_role secret key** → goes into `SUPABASE_KEY` (NOT the anon key)

### 2. Local secrets

Put them in `.env` at the project root (already gitignored):

```env
SUPABASE_URL=https://<your-project>.supabase.co
SUPABASE_KEY=<service_role secret>
```

`ara deploy` auto-discovers these and uploads them to the Ara backend as
runtime secrets.

### 3. Deploy

```bash
pip install ara-sdk
ara auth login
ara deploy personal_crm.py
```

Deploy output gives you a `runtime_key` — save it for Zapier.

### 4. Connect outbound channels

At [app.ara.so/connect](https://app.ara.so/connect):
- **Linq** (for iMessage texts)
- **Google Calendar** (for meeting lookup + birthday sync)

### 5. Enable the cron

In the Ara dashboard → your `personal-crm` agent → schedule, set:

```
*/10 * * * *
```

---

## Granola → Zapier integration

Granola's [Zapier integration](https://docs.granola.ai/help-center/sharing/integrations/zapier)
fires a webhook every time a meeting note is created. Wire that webhook to
this agent and every meeting flows straight into the CRM.

**Requires:** Granola paid plan (Zapier is paid-only) + Zapier account.

### Zap setup

**Trigger → Granola → Note Added to Folder**
- Connect your Granola account.
- Pick a folder (e.g. create one called `CRM`). Any note dropped in this
  folder fires the Zap.

**Action → Webhooks by Zapier → POST**

| Field | Value |
|---|---|
| **URL** | `https://api.ara.so/v1/apps/<YOUR_APP_ID>/run` |
| **Payload Type** | `JSON` |
| **Data** | See JSON below |
| **Headers** | `Authorization: Bearer <runtime_key>`<br>`Content-Type: application/json` |

JSON body (use Zapier's field-picker to substitute the Granola fields):

```json
{
  "agent_id": "personal-crm",
  "workflow_id": "personal-crm",
  "warmup": false,
  "input": {
    "message": "Meeting: {{title}}\nDate: {{created_at}}\nAttendees: {{attendees}}\n\n{{transcript}}"
  }
}
```

Turn the Zap on. From this point forward, every Granola note in that folder
becomes structured CRM data.

### Alternative trigger

If you'd rather hand-pick which meetings reach the CRM, swap the trigger to
**"Note Shared to Zapier"** — you click share in Granola's sidebar and only
those notes flow in.

---

## Data model

Four tables in Supabase. Small on purpose.

| Table | Purpose |
|---|---|
| `contacts` | The person. Name, company, relationship, email, phone, tags[], last_contact, created_at. |
| `contact_notes` | Freeform notes, many-to-one on contacts. Tagged with source (`meeting`, `granola`, `manual`, `email`). Drives the "reach out about what" suggestions. |
| `contact_dates` | Important dates (birthday, anniversary, follow-up). `synced_to_calendar` flag drives calendar reconciliation. |
| `reminded_meetings` | Meeting-ID dedup log so you don't get double-texted before the same meeting. |

Full schema: see [`schema.sql`](./schema.sql).

---

## Tools exposed to the agent

| Tool | Purpose |
|---|---|
| `add_contact` | Create a new contact (idempotent by name). |
| `append_note` | Add a dated note to a contact; auto-creates if missing. |
| `add_important_date` | Attach a birthday / anniversary / follow-up date. |
| `lookup_contact` | Full dossier for one person. |
| `lookup_contacts_bulk` | Dossiers for many — used for meeting reminders. |
| `list_contacts` | List everyone, optionally tag-filtered. |
| `upcoming_dates` | Birthdays / events in the next N days (birthdays recur annually). |
| `stale_contacts` | People you haven't talked to in N+ days, with their most recent note for context. |
| `pending_calendar_syncs` | Dates not yet on Google Calendar. |
| `mark_dates_synced` | Mark a date synced after a calendar insert. |
| `get_reminded_meetings` | Meeting IDs you've already been texted about. |
| `mark_meeting_reminded` | Record that a reminder text went out. |

Plus Ara's built-in connectors — `linq_send_message` for outbound iMessage
and the `google_calendar_*` tools for event reads/writes.

---

## Design decisions worth calling out

- **Stdlib-only Supabase client.** Avoids shipping `supabase-py` and its
  transitive deps into the Ara runtime. The HTTP surface we need
  (GET/POST/PATCH against PostgREST) is ~40 lines of `urllib.request`.
- **Source-of-truth is Supabase, not Ara.** The agent is stateless between
  cron ticks; every decision is made from what's in Postgres.
- **Ask, don't guess.** Ambiguous names trigger one disambiguation question
  over iMessage rather than silently creating duplicates or attaching notes
  to the wrong person.
- **Relevance over completeness.** The agent filters meeting notes down to
  1–3 bullets per attendee. The goal is recall, not archival.
- **Meeting-reminder dedup is persistent.** Because the cron runs every 10
  minutes and the "meetings in the next 30 min" window overlaps, dedup
  goes in Postgres (`reminded_meetings`) — not in-memory.

---

## Roadmap

- [ ] LinkedIn enrichment on `add_contact` (company/title backfill from URL).
- [ ] Weekly digest email with deeper trend data.
- [ ] "Who should I introduce?" tool — mutual-interest surfacing across
      contacts.
- [ ] Voice-note ingestion (Linq audio → transcript → same pipeline).
- [ ] Team mode — share contacts selectively between Ara agents.

---

## Credits

Built by [@NickMJohnson](https://github.com/NickMJohnson) for the
[Ara hackathon](https://ara.so), April 2026.
