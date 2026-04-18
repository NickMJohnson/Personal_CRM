-- Personal CRM — Supabase schema
-- Run this in the Supabase SQL editor once, then set SUPABASE_URL and
-- SUPABASE_KEY as secrets in app.ara.so for your personal-crm agent.

-- Enable the uuid extension (Supabase has it by default, but safe to repeat)
create extension if not exists "pgcrypto";

-- =============================================================================
-- Contacts
-- =============================================================================
create table if not exists contacts (
  id            uuid primary key default gen_random_uuid(),
  name          text not null,
  company       text default '',
  relationship  text default '',
  email         text default '',
  phone         text default '',
  tags          text[] default '{}',
  last_contact  date,
  created_at    timestamptz default now()
);

-- Case-insensitive name lookups
create index if not exists contacts_name_lower_idx on contacts (lower(name));

-- =============================================================================
-- Notes (many-to-one on contacts)
-- =============================================================================
create table if not exists contact_notes (
  id          uuid primary key default gen_random_uuid(),
  contact_id  uuid not null references contacts(id) on delete cascade,
  text        text not null,
  source      text default 'manual',
  at          timestamptz default now()
);

create index if not exists contact_notes_contact_idx
  on contact_notes (contact_id, at desc);

-- =============================================================================
-- Important dates (birthdays, anniversaries, follow-ups)
-- =============================================================================
create table if not exists contact_dates (
  id                   uuid primary key default gen_random_uuid(),
  contact_id           uuid not null references contacts(id) on delete cascade,
  label                text not null,
  date_iso             date not null,
  synced_to_calendar   boolean default false
);

create index if not exists contact_dates_contact_idx on contact_dates (contact_id);
create index if not exists contact_dates_pending_sync_idx
  on contact_dates (synced_to_calendar) where synced_to_calendar = false;

-- =============================================================================
-- Reminded meetings log (dedup for meeting-reminder texts)
-- =============================================================================
create table if not exists reminded_meetings (
  meeting_id  text primary key,
  title       text,
  at          timestamptz default now()
);
