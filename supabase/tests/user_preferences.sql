-- Run only on an isolated database with the preferences migration applied.
-- All fixtures and writes roll back, including if an assertion fails.
begin;
insert into auth.users (id) values
  ('00000000-0000-0000-0000-000000000001'),
  ('00000000-0000-0000-0000-000000000002');
insert into public.user_preferences (user_id, preferences, revision) values
  ('00000000-0000-0000-0000-000000000001',
   '{"closet_categories":["men"],"age":null,"top_fit":"relaxed","pants_fit":"baggy","locale":"he"}', 1),
  ('00000000-0000-0000-0000-000000000002',
   '{"closet_categories":[],"age":65,"top_fit":"slim","pants_fit":"skinny","locale":"en"}', 1);

set local role anon;
do $$ begin
  begin
    perform 1 from public.user_preferences;
    raise exception 'Anonymous reads must be denied';
  exception when insufficient_privilege then null;
  end;
end $$;
reset role;
select set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-000000000001', true);
set local role authenticated;
do $$ begin
  if (select count(*) from public.user_preferences) <> 1 then
    raise exception 'Owner must see exactly one profile';
  end if;
  if exists (select 1 from public.user_preferences
    where user_id = '00000000-0000-0000-0000-000000000002') then
    raise exception 'Non-owner profile leaked';
  end if;
  begin
    update public.user_preferences set revision = 2;
    raise exception 'Direct client updates must be denied';
  exception when insufficient_privilege then null;
  end;
  begin
    delete from public.user_preferences;
    raise exception 'Direct client deletes must be denied';
  exception when insufficient_privilege then null;
  end;
  begin
    insert into public.user_preferences (user_id, preferences, revision)
      values ('00000000-0000-0000-0000-000000000001', '{}', 1);
    raise exception 'Direct client inserts must be denied';
  exception when insufficient_privilege then null;
  end;
  if has_function_privilege('authenticated', 'public.touch_user_preferences()', 'execute') then
    raise exception 'Client must not directly execute the trigger';
  end if;
end $$;
reset role;
select set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-000000000002', true);
set local role authenticated;
do $$ begin
  if (select preferences->>'locale' from public.user_preferences) <> 'en' then
    raise exception 'Second account saw the wrong preferences';
  end if;
end $$;
reset role;
set local role service_role;
do $$ declare affected integer; invalid jsonb; original jsonb; begin
  if (select count(*) from public.user_preferences) <> 2 then
    raise exception 'Server role must be able to read both profiles';
  end if;
  update public.user_preferences set revision = 2, updated_at = '2000-01-01'
    where user_id = '00000000-0000-0000-0000-000000000001' and revision = 1;
  get diagnostics affected = row_count;
  if affected <> 1 then raise exception 'First revision update must succeed'; end if;
  update public.user_preferences set revision = 3
    where user_id = '00000000-0000-0000-0000-000000000001' and revision = 1;
  get diagnostics affected = row_count;
  if affected <> 0 then raise exception 'Stale revision must not overwrite'; end if;
  if exists (select 1 from public.user_preferences where updated_at < '2020-01-01') then
    raise exception 'Timestamp trigger did not run';
  end if;
  select preferences into original from public.user_preferences
    where user_id = '00000000-0000-0000-0000-000000000001';
  for invalid in select value from jsonb_array_elements('[
    {},
    {"age":12}, {"age":66}, {"age":true}, {"age":"25"},
    {"age":25.5}, {"top_fit":null}, {"locale":"fr"},
    {"closet_categories":["men","men"]}, {"user_id":"someone"}
  ]') loop
    begin
      update public.user_preferences set preferences =
        case when invalid = '{}'::jsonb then '{}'::jsonb else original || invalid end
        where user_id = '00000000-0000-0000-0000-000000000001';
      raise exception 'Invalid preferences accepted: %', invalid;
    exception when check_violation then null;
    end;
  end loop;
end $$;
reset role;
delete from auth.users where id = '00000000-0000-0000-0000-000000000001';
do $$ begin
  if (select count(*) from public.user_preferences) <> 1 then
    raise exception 'Account deletion must cascade to preferences';
  end if;
end $$;
rollback;
