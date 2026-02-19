-- p0_rls_ses_feedback_verify.sql
-- Verification queries for RLS patch on public.ses_feedback_events
-- Run after: 20260220_fix_ses_feedback_events_rls_and_guard.sql

-- B1) Table RLS status (expect: rls_enabled = true)
select
    n.nspname        as schema,
    c.relname        as table_name,
    c.relrowsecurity as rls_enabled
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public'
  and c.relname = 'ses_feedback_events';

-- B2) Role grants on table
--     Expect: NO rows for anon or authenticated
--     Expect: rows for service_role and postgres (SELECT/INSERT/UPDATE/DELETE)
select
    table_schema,
    table_name,
    grantee,
    privilege_type
from information_schema.role_table_grants
where table_schema = 'public'
  and table_name   = 'ses_feedback_events'
order by grantee, privilege_type;

-- B3) Event trigger installation (expect: evtenabled = 'O' = enabled)
select
    evtname,
    evtenabled,
    evtevent,
    evtfoid::regproc as function_name
from pg_event_trigger
where evtname = 'dp_ensure_rls_public';

-- B4) anon access block check
--     Uncomment to verify manually; requires superuser or SET ROLE privilege.
-- set role anon;
-- select count(*) from public.ses_feedback_events;  -- expect: ERROR: permission denied
-- reset role;
