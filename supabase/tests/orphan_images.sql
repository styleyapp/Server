-- Run against a fresh isolated database only: these are synthetic Storage metadata.
begin;
insert into auth.users(id) values('00000000-0000-0000-0000-000000000075');
insert into storage.objects(bucket_id,name,created_at) values
 ('wardrobe-items','00000000-0000-0000-0000-000000000075/retry.png',now()-interval '2 days'),
 ('wardrobe-items','00000000-0000-0000-0000-000000000076/old.png',now()-interval '2 days'),
 ('wardrobe-items','00000000-0000-0000-0000-000000000076/recent.png',now());
set local role service_role;
do $$ begin
 assert private.cleanup_owner_exists('00000000-0000-0000-0000-000000000075');
 assert not private.cleanup_owner_exists('00000000-0000-0000-0000-000000000076');
 assert (select count(*) from public.orphaned_wardrobe_images())=1;
 assert (select name from public.orphaned_wardrobe_images())='00000000-0000-0000-0000-000000000076/old.png';
 assert not has_function_privilege('authenticated','private.cleanup_owner_exists(text)','execute');
 assert not has_function_privilege('anon','private.cleanup_owner_exists(text)','execute');
end $$;
reset role;
rollback;
