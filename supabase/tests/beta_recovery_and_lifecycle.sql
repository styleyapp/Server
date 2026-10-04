begin;
insert into auth.users(id) values('00000000-0000-0000-0000-000000000071');
do $$ declare result jsonb; old_lease uuid; begin
 result=public.claim_scan('00000000-0000-0000-0000-000000000071','10000000-0000-0000-0000-000000000071',repeat('a',64));
 assert result->>'status'='claimed';old_lease=(result->>'lease_token')::uuid;
 result=public.claim_scan('00000000-0000-0000-0000-000000000071','10000000-0000-0000-0000-000000000071',repeat('b',64));
 assert result->>'status'='conflict';
 result=public.claim_scan('00000000-0000-0000-0000-000000000071','10000000-0000-0000-0000-000000000072',repeat('a',64));
 assert result->>'status'='generation_in_progress';
 update public.scan_requests set lease_until=now()-interval '1 second';
 result=public.claim_scan('00000000-0000-0000-0000-000000000071','10000000-0000-0000-0000-000000000071',repeat('a',64));
 assert result->>'status'='claimed' and (result->>'lease_token')::uuid<>old_lease;
 update public.scan_requests set state='ready';
 result=public.claim_scan('00000000-0000-0000-0000-000000000071','10000000-0000-0000-0000-000000000071',repeat('a',64));
 assert result->>'status'='ready';
 assert not has_table_privilege('anon','public.scan_requests','select');
 assert not has_table_privilege('authenticated','public.scan_requests','insert');
 assert not has_function_privilege('authenticated','public.claim_scan(uuid,uuid,text)','execute');
 assert not has_table_privilege('authenticated','public.account_deletions','select');

 update public.scan_requests set state='failed',attempts=3;
 result=public.claim_scan('00000000-0000-0000-0000-000000000071','10000000-0000-0000-0000-000000000071',repeat('a',64));
 assert result->>'status'='rate_limited';
 update public.scan_requests set expires_at=now()-interval '1 second';
 result=public.claim_scan('00000000-0000-0000-0000-000000000071','10000000-0000-0000-0000-000000000071',repeat('a',64));
 assert result->>'status'='expired';
 delete from public.scan_requests;
 insert into public.scan_requests(user_id,request_id,content_hash,state)
 select '00000000-0000-0000-0000-000000000071',gen_random_uuid(),repeat('a',64),'ready' from generate_series(1,6);
 result=public.claim_scan('00000000-0000-0000-0000-000000000071',gen_random_uuid(),repeat('a',64));
 assert result->>'status'='rate_limited';
 update public.scan_requests set created_at=now()-interval '2 hours';
 insert into public.scan_requests(user_id,request_id,content_hash,state,created_at)
 select '00000000-0000-0000-0000-000000000071',gen_random_uuid(),repeat('a',64),'ready',now()-interval '2 hours' from generate_series(1,18);
 result=public.claim_scan('00000000-0000-0000-0000-000000000071',gen_random_uuid(),repeat('a',64));
 assert result->>'status'='rate_limited';
 assert not has_function_privilege('authenticated','public.orphaned_wardrobe_images()','execute');
 assert not has_function_privilege('authenticated','public.account_storage_paths(uuid,text)','execute');
 insert into public.account_deletions(user_id) values('00000000-0000-0000-0000-000000000071');
 result=public.claim_scan('00000000-0000-0000-0000-000000000071','10000000-0000-0000-0000-000000000072',repeat('a',64));
 assert result->>'status'='unauthorized';
 begin
  insert into public.wardrobe_items(user_id,name,source,source_candidate_id,image_path)
   values('00000000-0000-0000-0000-000000000071','Blocked','photo',gen_random_uuid(),'blocked.png');
  raise exception 'Deleting owner could create a garment';
 exception when check_violation then null; end;
 delete from auth.users where id='00000000-0000-0000-0000-000000000071';
 assert not exists(select 1 from public.scan_requests where user_id='00000000-0000-0000-0000-000000000071');
 assert exists(select 1 from public.account_deletions where user_id='00000000-0000-0000-0000-000000000071');
end $$;
insert into auth.users(id) values('00000000-0000-0000-0000-000000000072');
insert into public.wardrobe_items(user_id,name,source,source_candidate_id,image_path)
values('00000000-0000-0000-0000-000000000072','Visible before deletion','photo',gen_random_uuid(),'active-test.png');
select set_config('request.jwt.claim.sub','00000000-0000-0000-0000-000000000072',true);
set local role authenticated;
do $$ begin
 assert private.account_is_active('00000000-0000-0000-0000-000000000072');
 assert not private.account_is_active('00000000-0000-0000-0000-000000000073');
 assert (select count(*) from public.wardrobe_items)=1;
end $$;
reset role;
insert into public.account_deletions(user_id) values('00000000-0000-0000-0000-000000000072');
set local role authenticated;
do $$ begin
 assert not private.account_is_active('00000000-0000-0000-0000-000000000072');
 assert (select count(*) from public.wardrobe_items)=0;
end $$;
reset role;
rollback;
