-- Draft generations and saved outfits share durable identity. Clients cannot write directly.
create table public.outfits (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  request_id uuid not null,
  request_hash text not null check (request_hash ~ '^[0-9a-f]{64}$'),
  context jsonb not null check (jsonb_typeof(context) = 'object'),
  generation_version text not null default 'outfit-v1' check (char_length(generation_version) between 1 and 80),
  state text not null default 'pending' check (state in ('pending', 'ready', 'failed')),
  lease_token uuid not null default gen_random_uuid(),
  lease_until timestamptz not null default (now() + interval '90 seconds'),
  attempts integer not null default 1 check (attempts between 1 and 3),
  failure_code text,
  title_en text not null default '' check (char_length(title_en) <= 120),
  title_he text not null default '' check (char_length(title_he) <= 120),
  reason_en text not null default '' check (char_length(reason_en) <= 500),
  reason_he text not null default '' check (char_length(reason_he) <= 500),
  created_at timestamptz not null default now(),
  saved_at timestamptz,
  unique(user_id, request_id),
  unique(id, user_id),
  check (saved_at is null or state = 'ready')
);
create index outfits_user_saved_idx on public.outfits(user_id, saved_at desc, id desc)
  where saved_at is not null;
create index outfits_user_created_idx on public.outfits(user_id, created_at desc);
-- Composite references prevent a piece from belonging to another account even with service access.
create unique index wardrobe_items_id_owner_idx on public.wardrobe_items(id, user_id);
create table public.outfit_pieces (
  outfit_id uuid not null,
  user_id uuid not null,
  position smallint not null check (position between 0 and 5),
  slot text not null check (slot in ('top','bottom','one_piece','outerwear','shoes','accessory')),
  garment_id uuid not null,
  wardrobe_item_id uuid,
  snapshot jsonb not null check (jsonb_typeof(snapshot) = 'object'),
  primary key(outfit_id, position),
  unique(outfit_id, garment_id),
  foreign key(outfit_id, user_id) references public.outfits(id, user_id) on delete cascade,
  foreign key(wardrobe_item_id, user_id) references public.wardrobe_items(id, user_id)
    on delete set null (wardrobe_item_id),
  check (wardrobe_item_id is null or wardrobe_item_id = garment_id)
);
create index outfit_pieces_live_item_idx on public.outfit_pieces(wardrobe_item_id, user_id);
alter table public.outfits enable row level security;
alter table public.outfit_pieces enable row level security;
revoke all on public.outfits, public.outfit_pieces from public, anon, authenticated;
grant select on public.outfits, public.outfit_pieces to authenticated;
grant select, insert, update, delete on public.outfits, public.outfit_pieces to service_role;
create policy "Owners read outfits" on public.outfits for select to authenticated
  using ((select auth.uid()) = user_id);
create policy "Owners read outfit pieces" on public.outfit_pieces for select to authenticated
  using ((select auth.uid()) = user_id);

-- Serializes claims per account: one active generation, 10 new requests/hour, 3 attempts/request.
create function public.claim_outfit(p_user uuid, p_request uuid, p_hash text, p_context jsonb, p_version text default 'outfit-v1')
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare target public.outfits; begin
  perform pg_advisory_xact_lock(hashtextextended(p_user::text, 0));
  select * into target from public.outfits
    where user_id = p_user and request_id = p_request for update;
  if found then
    if target.request_hash <> p_hash then return jsonb_build_object('status','conflict'); end if;
    if target.state = 'ready' then
      return jsonb_build_object('status','ready','id',target.id);
    end if;
    if target.state = 'pending' and target.lease_until > now() then
      return jsonb_build_object('status','generation_in_progress');
    end if;
    if target.attempts >= 3 then return jsonb_build_object('status','rate_limited'); end if;
  elsif (select count(*) from public.outfits where user_id=p_user
    and created_at > now()-interval '1 hour') >= 10 then
    return jsonb_build_object('status','rate_limited');
  end if;
  if exists(select 1 from public.outfits where user_id=p_user and state='pending'
    and lease_until > now() and request_id <> p_request) then
    return jsonb_build_object('status','generation_in_progress');
  end if;
  if target.id is null then
    insert into public.outfits(user_id, request_id, request_hash, context, generation_version)
      values(p_user,p_request,p_hash,p_context,p_version) returning * into target;
  else
    update public.outfits set state='pending', lease_token=gen_random_uuid(),
      lease_until=now()+interval '90 seconds', attempts=attempts+1, failure_code=null, generation_version=p_version
      where id=target.id returning * into target;
  end if;
  return jsonb_build_object('status','claimed','id',target.id,'lease_token',target.lease_token);
end $$;

create function public.complete_outfit(p_user uuid, p_id uuid, p_lease uuid, p_proposal jsonb)
returns text language plpgsql security invoker set search_path = '' as $$
declare target public.outfits; piece jsonb; garment public.wardrobe_items;
  n integer := 0; total integer; top_count integer; bottom_count integer; one_count integer;
begin
  select * into target from public.outfits where id=p_id and user_id=p_user for update;
  if not found then return 'not_found'; end if;
  if target.state <> 'pending' or target.lease_token <> p_lease or target.lease_until <= now() then
    return 'generation_in_progress';
  end if;
  if jsonb_typeof(p_proposal->'pieces') <> 'array' then return 'invalid_proposal'; end if;
  total := jsonb_array_length(p_proposal->'pieces');
  select count(*) filter(where value->>'slot'='top'),
    count(*) filter(where value->>'slot'='bottom'),
    count(*) filter(where value->>'slot'='one_piece')
    into top_count,bottom_count,one_count from jsonb_array_elements(p_proposal->'pieces');
  if total not between 1 and 6 or not (
    (top_count=1 and bottom_count=1 and one_count=0) or
    (top_count=0 and bottom_count=0 and one_count=1)) then return 'invalid_proposal'; end if;
  if exists(select 1 from jsonb_array_elements(p_proposal->'pieces')
    group by value->>'slot' having count(*) > case when value->>'slot'='accessory' then 2 else 1 end)
    or exists(select 1 from jsonb_array_elements(p_proposal->'pieces')
      group by value->>'garment_id' having count(*) > 1) then return 'invalid_proposal'; end if;
  -- Lock all live garments in a deterministic order, preventing deletion while committing pieces.
  perform 1 from public.wardrobe_items w where w.user_id=p_user and w.id in
    (select (value->>'garment_id')::uuid from jsonb_array_elements(p_proposal->'pieces'))
    order by w.id for share;
  if (select count(*) from public.wardrobe_items w where w.user_id=p_user and w.id in
    (select (value->>'garment_id')::uuid from jsonb_array_elements(p_proposal->'pieces'))) <> total then
    return 'wardrobe_changed';
  end if;
  for piece in select value from jsonb_array_elements(p_proposal->'pieces') loop
    select * into garment from public.wardrobe_items
      where id=(piece->>'garment_id')::uuid and user_id=p_user;
    insert into public.outfit_pieces(outfit_id,user_id,position,slot,garment_id,wardrobe_item_id,snapshot)
      values(p_id,p_user,n,piece->>'slot',garment.id,garment.id,
        jsonb_build_object('name',garment.name,'brand',garment.brand,'category',garment.category,
          'type',garment.type,'color',garment.color,'season',garment.season,'tags',garment.tags,
          'hebrew',garment.hebrew,'length',garment.length));
    n := n+1;
  end loop;
  update public.outfits set state='ready',
    title_en=p_proposal->>'title_en', title_he=p_proposal->>'title_he',
    reason_en=p_proposal->>'reason_en', reason_he=p_proposal->>'reason_he'
    where id=p_id;
  return 'ready';
end $$;

create function public.fail_outfit(p_user uuid, p_id uuid, p_lease uuid, p_code text)
returns void language sql security invoker set search_path = '' as $$
  update public.outfits set state='failed',failure_code=p_code
    where id=p_id and user_id=p_user and state='pending' and lease_token=p_lease;
$$;

create function public.save_outfit(p_user uuid, p_id uuid)
returns text language plpgsql security invoker set search_path = '' as $$
declare target public.outfits; total integer; live_count integer; begin
  select * into target from public.outfits where id=p_id and user_id=p_user for update;
  if not found then return 'not_found'; end if;
  if target.saved_at is not null then return 'saved'; end if;
  if target.state <> 'ready' then return 'generation_in_progress'; end if;
  perform 1 from public.wardrobe_items w join public.outfit_pieces p
    on p.wardrobe_item_id=w.id and p.user_id=w.user_id
    where p.outfit_id=p_id and p.user_id=p_user order by w.id for share of w;
  select count(*),count(wardrobe_item_id) into total,live_count
    from public.outfit_pieces where outfit_id=p_id and user_id=p_user;
  if total=0 or total<>live_count then return 'wardrobe_changed'; end if;
  update public.outfits set saved_at=now() where id=p_id;
  return 'saved';
end $$;
revoke all on function public.claim_outfit(uuid,uuid,text,jsonb,text),
  public.complete_outfit(uuid,uuid,uuid,jsonb),public.fail_outfit(uuid,uuid,uuid,text),
  public.save_outfit(uuid,uuid) from public, anon, authenticated;
grant execute on function public.claim_outfit(uuid,uuid,text,jsonb,text),
  public.complete_outfit(uuid,uuid,uuid,jsonb),public.fail_outfit(uuid,uuid,uuid,text),
  public.save_outfit(uuid,uuid) to service_role;
