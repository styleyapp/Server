-- Run only on an isolated database with both wardrobe migrations applied.
-- Fixed fixtures and every write are rolled back.
begin;
insert into auth.users (id) values
  ('00000000-0000-0000-0000-000000000001'),
  ('00000000-0000-0000-0000-000000000002');
insert into public.wardrobe_items
  (id, user_id, name, source, source_candidate_id, image_path) values
  ('10000000-0000-0000-0000-000000000001',
   '00000000-0000-0000-0000-000000000001', 'Legacy shirt', 'photo',
   '20000000-0000-0000-0000-000000000001', 'test/first.png'),
  ('10000000-0000-0000-0000-000000000002',
   '00000000-0000-0000-0000-000000000002', 'Other shirt', 'photo',
   '20000000-0000-0000-0000-000000000002', 'test/second.png');

do $$ begin
  if exists (select 1 from public.wardrobe_items
    where hebrew is not null or length <> '' or is_favorite <> false) then
    raise exception 'Legacy insert defaults changed';
  end if;
end $$;

set local role anon;
do $$ begin
  if exists (select 1 from public.wardrobe_items) then
    raise exception 'Anonymous wardrobe read leaked data';
  end if;
  begin
    insert into public.wardrobe_items
      (user_id, name, source, source_candidate_id, image_path) values
      ('00000000-0000-0000-0000-000000000001', 'Anonymous', 'photo',
       '20000000-0000-0000-0000-000000000003', 'test/anonymous.png');
    raise exception 'Anonymous wardrobe insert allowed';
  exception when insufficient_privilege then null;
  end;
  update public.wardrobe_items set is_favorite = true;
  if found then raise exception 'Anonymous wardrobe update allowed'; end if;
  delete from public.wardrobe_items;
  if found then raise exception 'Anonymous wardrobe delete allowed'; end if;
end $$;
reset role;

select set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-000000000001', true);
set local role authenticated;
do $$ begin
  if (select count(*) from public.wardrobe_items) <> 1 then
    raise exception 'Owner isolation failed';
  end if;
  update public.wardrobe_items
    set hebrew = '{"name":"חולצה","tags":["יומיומי"]}', length = 'regular', is_favorite = true
    where id = '10000000-0000-0000-0000-000000000001';
  if not found then raise exception 'Owner update failed'; end if;
  if not exists (select 1 from public.wardrobe_items
    where name = 'Legacy shirt' and image_path = 'test/first.png'
      and hebrew->>'name' = 'חולצה' and length = 'regular' and is_favorite) then
    raise exception 'Bilingual/favorite update changed existing metadata';
  end if;
  update public.wardrobe_items set is_favorite = true
    where id = '10000000-0000-0000-0000-000000000002';
  if found then raise exception 'Other owner favorite changed'; end if;
  delete from public.wardrobe_items
    where id = '10000000-0000-0000-0000-000000000002';
  if found then raise exception 'Other owner item deleted'; end if;
  begin
    update public.wardrobe_items set user_id = '00000000-0000-0000-0000-000000000002';
    raise exception 'Owner reassignment allowed';
  exception when insufficient_privilege then null;
  end;
end $$;
reset role;

set local role service_role;
do $$ declare invalid jsonb; valid_length text; begin
  if (select count(*) from public.wardrobe_items) <> 2 then
    raise exception 'Server cannot read both owners';
  end if;
  if exists (select 1 from public.wardrobe_items
    where id = '10000000-0000-0000-0000-000000000002' and is_favorite) then
    raise exception 'Non-owner write was persisted';
  end if;
  foreach valid_length in array array['', 'short', 'regular', 'long'] loop
    update public.wardrobe_items set length = valid_length
      where id = '10000000-0000-0000-0000-000000000001';
  end loop;
  begin
    update public.wardrobe_items set length = 'invalid';
    raise exception 'Invalid garment length accepted';
  exception when check_violation then null;
  end;
  for invalid in select value from jsonb_array_elements('[[], "text", 42, true, null]') loop
    begin
      update public.wardrobe_items set hebrew = invalid;
      raise exception 'Invalid Hebrew labels accepted';
    exception when check_violation then null;
    end;
  end loop;
  begin
    update public.wardrobe_items set is_favorite = null;
    raise exception 'Null favorite accepted';
  exception when not_null_violation then null;
  end;
  update public.wardrobe_items set hebrew = null, is_favorite = false;
  if exists (select 1 from public.wardrobe_items where hebrew is not null or is_favorite) then
    raise exception 'Clearing optional labels/favorite failed';
  end if;
end $$;
reset role;
rollback;
