-- Isolated database only: fixed fixtures, all writes roll back.
begin;
insert into auth.users(id) values
 ('00000000-0000-0000-0000-000000000001'),('00000000-0000-0000-0000-000000000002');
insert into public.wardrobe_items(id,user_id,name,source,source_candidate_id,image_path) values
 ('10000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000001','Shirt','photo',gen_random_uuid(),'test/shirt.png'),
 ('10000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000001','Pants','photo',gen_random_uuid(),'test/pants.png'),
 ('10000000-0000-0000-0000-000000000003','00000000-0000-0000-0000-000000000002','Other dress','photo',gen_random_uuid(),'test/other.png');
set local role service_role;
do $$ declare c jsonb; again jsonb; other jsonb; body jsonb; oid uuid; lease uuid; saved timestamptz;
begin
 c:=public.claim_outfit('00000000-0000-0000-0000-000000000001',
   '20000000-0000-0000-0000-000000000001',repeat('a',64),'{}');
 if c->>'status'<>'claimed' then raise exception 'Claim failed'; end if;
 oid:=(c->>'id')::uuid; lease:=(c->>'lease_token')::uuid;
 again:=public.claim_outfit('00000000-0000-0000-0000-000000000001',
   '20000000-0000-0000-0000-000000000001',repeat('a',64),'{}');
 if again->>'status'<>'generation_in_progress' then raise exception 'Duplicate lease allowed'; end if;
 again:=public.claim_outfit('00000000-0000-0000-0000-000000000001',
   '20000000-0000-0000-0000-000000000001',repeat('b',64),'{}');
 if again->>'status'<>'conflict' then raise exception 'Changed idempotency payload accepted'; end if;
 body:='{"title_en":"Owned outfit","title_he":"לוק","reason_en":"Simple pair","reason_he":"שילוב","pieces":[{"slot":"top","garment_id":"10000000-0000-0000-0000-000000000001"},{"slot":"bottom","garment_id":"10000000-0000-0000-0000-000000000002"}]}';
 if public.complete_outfit('00000000-0000-0000-0000-000000000001',oid,gen_random_uuid(),body)<>'generation_in_progress' then
  raise exception 'Wrong worker lease accepted'; end if;
 if public.complete_outfit('00000000-0000-0000-0000-000000000001',oid,lease,
  jsonb_set(body,'{pieces,1,garment_id}','"10000000-0000-0000-0000-000000000003"'))<>'wardrobe_changed' then
  raise exception 'Foreign owner piece accepted'; end if;
 if exists(select 1 from public.outfit_pieces) then raise exception 'Partial pieces committed'; end if;
 if public.complete_outfit('00000000-0000-0000-0000-000000000001',oid,lease,body)<>'ready' then
  raise exception 'Owned pair failed'; end if;
 again:=public.claim_outfit('00000000-0000-0000-0000-000000000001',
   '20000000-0000-0000-0000-000000000001',repeat('a',64),'{}');
 if again->>'status'<>'ready' or (again->>'id')::uuid<>oid then raise exception 'Retry lost identity'; end if;
 if public.save_outfit('00000000-0000-0000-0000-000000000002',oid)<>'not_found' then
  raise exception 'Other owner saved outfit'; end if;
 if public.save_outfit('00000000-0000-0000-0000-000000000001',oid)<>'saved' then
  raise exception 'Save failed'; end if;
 select saved_at into saved from public.outfits where id=oid;
 perform public.save_outfit('00000000-0000-0000-0000-000000000001',oid);
 if (select saved_at from public.outfits where id=oid)<>saved then raise exception 'Retry changed save date'; end if;
 update public.wardrobe_items set name='Edited shirt' where id='10000000-0000-0000-0000-000000000001';
 if (select snapshot->>'name' from public.outfit_pieces where outfit_id=oid and slot='top')<>'Shirt' then
  raise exception 'Historical snapshot overwritten'; end if;
 delete from public.wardrobe_items where id='10000000-0000-0000-0000-000000000001';
 if (select count(*) from public.outfit_pieces where outfit_id=oid)<>2 or not exists
  (select 1 from public.outfit_pieces where outfit_id=oid and slot='top' and wardrobe_item_id is null) then
  raise exception 'Deleted piece did not become unavailable'; end if;
 if public.save_outfit('00000000-0000-0000-0000-000000000001',oid)<>'saved' then
  raise exception 'Saved outfit retry failed after deletion'; end if;
 -- Deleted draft pieces cannot be saved, and exhausted leases cannot commit.
 other:=public.claim_outfit('00000000-0000-0000-0000-000000000002',
   '20000000-0000-0000-0000-000000000002',repeat('c',64),'{}');
 if public.complete_outfit('00000000-0000-0000-0000-000000000002',(other->>'id')::uuid,
   (other->>'lease_token')::uuid,
   '{"title_en":"Dress","title_he":"שמלה","reason_en":"One piece","reason_he":"פריט","pieces":[{"slot":"one_piece","garment_id":"10000000-0000-0000-0000-000000000003"}]}')<>'ready' then
  raise exception 'One-piece outfit failed'; end if;
 delete from public.wardrobe_items where id='10000000-0000-0000-0000-000000000003';
 if public.save_outfit('00000000-0000-0000-0000-000000000002',(other->>'id')::uuid)<>'wardrobe_changed' then
  raise exception 'Unavailable draft saved'; end if;
end $$;

reset role;
insert into auth.users(id) values ('00000000-0000-0000-0000-000000000003');
set local role service_role;
do $$ declare c jsonb; second jsonb; original_lease uuid; identity uuid; request uuid; n integer;
begin
 request:=gen_random_uuid();
 c:=public.claim_outfit('00000000-0000-0000-0000-000000000003',request,repeat('d',64),'{}');
 identity:=(c->>'id')::uuid; original_lease:=(c->>'lease_token')::uuid;
 second:=public.claim_outfit('00000000-0000-0000-0000-000000000003',gen_random_uuid(),repeat('d',64),'{}');
 if second->>'status'<>'generation_in_progress' then raise exception 'Multiple paid jobs allowed'; end if;
 update public.outfits set lease_until=now()-interval '1 second' where id=identity;
 c:=public.claim_outfit('00000000-0000-0000-0000-000000000003',request,repeat('d',64),'{}','outfit-test-v2');
 if c->>'status'<>'claimed' or (c->>'lease_token')::uuid=original_lease then
  raise exception 'Expired lease did not recover'; end if;
 if (select generation_version from public.outfits where id=identity)<>'outfit-test-v2' then
  raise exception 'Retry version was not recorded'; end if;
 if public.complete_outfit('00000000-0000-0000-0000-000000000003',identity,original_lease,'{}')<>'generation_in_progress' then
  raise exception 'Expired worker still writes'; end if;
 perform public.fail_outfit('00000000-0000-0000-0000-000000000003',identity,(c->>'lease_token')::uuid,'provider_unavailable');
 c:=public.claim_outfit('00000000-0000-0000-0000-000000000003',request,repeat('d',64),'{}');
 perform public.fail_outfit('00000000-0000-0000-0000-000000000003',identity,(c->>'lease_token')::uuid,'provider_unavailable');
 c:=public.claim_outfit('00000000-0000-0000-0000-000000000003',request,repeat('d',64),'{}');
 if c->>'status'<>'rate_limited' then raise exception 'Attempt limit exceeded'; end if;
 for n in 1..9 loop
  c:=public.claim_outfit('00000000-0000-0000-0000-000000000003',gen_random_uuid(),repeat('d',64),'{}');
  if c->>'status'<>'claimed' then raise exception 'Valid hourly claim rejected'; end if;
  perform public.fail_outfit('00000000-0000-0000-0000-000000000003',(c->>'id')::uuid,(c->>'lease_token')::uuid,'provider_unavailable');
 end loop;
 c:=public.claim_outfit('00000000-0000-0000-0000-000000000003',gen_random_uuid(),repeat('d',64),'{}');
 if c->>'status'<>'rate_limited' then raise exception 'Hourly cost limit exceeded'; end if;
end $$;

reset role;
set local role anon;
do $$ begin
 begin perform 1 from public.outfits; raise exception 'Anonymous outfit read allowed';
 exception when insufficient_privilege then null; end;
 if has_function_privilege('anon','public.claim_outfit(uuid,uuid,text,jsonb,text)','execute') then
  raise exception 'Anonymous generation RPC exposed'; end if;
end $$;
reset role;
select set_config('request.jwt.claim.sub','00000000-0000-0000-0000-000000000001',true);
set local role authenticated;
do $$ begin
 if (select count(*) from public.outfits)<>1 or (select count(*) from public.outfit_pieces)<>2 then
  raise exception 'Outfit owner isolation failed'; end if;
 begin update public.outfits set saved_at=null; raise exception 'Client direct mutation allowed';
 exception when insufficient_privilege then null; end;
 delete from public.wardrobe_items where id='10000000-0000-0000-0000-000000000002';
 if not found then raise exception 'Owner garment deletion failed with saved references'; end if;
 begin delete from public.outfit_pieces; raise exception 'Client piece deletion allowed';
 exception when insufficient_privilege then null; end;
 if has_function_privilege('authenticated','public.save_outfit(uuid,uuid)','execute') then
  raise exception 'Authenticated privileged RPC exposed'; end if;
end $$;
reset role;
delete from auth.users where id='00000000-0000-0000-0000-000000000001';
do $$ begin
 if exists(select 1 from public.outfit_pieces where user_id='00000000-0000-0000-0000-000000000001') then
  raise exception 'Account deletion did not cascade'; end if;
end $$;
rollback;
