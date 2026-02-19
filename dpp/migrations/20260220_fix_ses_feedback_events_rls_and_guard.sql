-- Phase 3 patch: Enable RLS on ses_feedback_events + P1-B event trigger guard
-- Created: 2026-02-20
-- Idempotent: safe to re-run.

begin;

-- A2) Enable RLS on public.ses_feedback_events (removes Supabase dashboard warning)
do $$
begin
  if to_regclass('public.ses_feedback_events') is not null then
    execute 'alter table public.ses_feedback_events enable row level security';
  end if;
end $$;

-- A3) Minimal permissions: block Data API roles (anon/authenticated),
--     retain access for service_role and postgres.
do $$
begin
  if to_regclass('public.ses_feedback_events') is not null then
    execute 'revoke all on table public.ses_feedback_events from anon';
    execute 'revoke all on table public.ses_feedback_events from authenticated';
    execute 'grant select, insert, update, delete on table public.ses_feedback_events to service_role';
    execute 'grant select, insert, update, delete on table public.ses_feedback_events to postgres';
  end if;
end $$;

-- A4) P1-B: Event trigger — auto-enable RLS on any new public table.
--     NOTE: does NOT apply retroactively; A2 above covers the existing table.
create or replace function dp_rls_auto_enable_public()
returns event_trigger
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare r record;
begin
  for r in
    select *
    from pg_event_trigger_ddl_commands()
    where command_tag in ('CREATE TABLE','CREATE TABLE AS','SELECT INTO')
      and object_type in ('table','partitioned table')
  loop
    if r.schema_name = 'public' then
      begin
        execute format('alter table if exists %s enable row level security', r.object_identity);
      exception when others then
        raise notice 'dp_rls_auto_enable_public: failed for %', r.object_identity;
      end;
    end if;
  end loop;
end;
$$;

drop event trigger if exists dp_ensure_rls_public;
create event trigger dp_ensure_rls_public
  on ddl_command_end
  when tag in ('CREATE TABLE','CREATE TABLE AS','SELECT INTO')
  execute function dp_rls_auto_enable_public();

commit;
