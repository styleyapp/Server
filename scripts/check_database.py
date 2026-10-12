"""Isolated Postgres migration/role regression runner. Never accepts a non-local host."""

import argparse
import subprocess
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = """
create role anon; create role authenticated; create role service_role bypassrls;
create schema auth; create table auth.users(id uuid primary key);
create function auth.uid() returns uuid language sql stable as
 'select nullif(current_setting(''request.jwt.claim.sub'', true), '''')::uuid';
grant usage on schema auth to anon,authenticated,service_role;
grant execute on function auth.uid() to anon,authenticated,service_role;
create schema storage;
create table storage.buckets(id text primary key,name text,public boolean,
 file_size_limit bigint,allowed_mime_types text[]);
create table storage.objects(bucket_id text,name text,created_at timestamptz default now());
grant usage on schema storage to service_role;
grant select on storage.objects to service_role;
create function storage.foldername(text) returns text[] language sql immutable as
 'select string_to_array($1,''/'')';
"""


def run(url):
    if urlparse(url).hostname not in {"localhost", "127.0.0.1"}:
        raise SystemExit("Use a fresh isolated local test database only")

    def execute(sql):
        subprocess.run(
            ["psql", url, "--set=ON_ERROR_STOP=1", "--quiet"], input=sql, text=True, check=True
        )

    execute(BOOTSTRAP)
    for file in sorted((ROOT / "supabase/migrations").glob("*.sql")):
        if "restrict_rls_auto_enable" in file.name:
            continue  # Hosted-platform function only.
        execute(file.read_text())
        if "wardrobe_items" in file.name:
            execute(
                "grant select,insert,update,delete on public.wardrobe_items "
                "to anon,authenticated,service_role;"
            )
    for file in sorted((ROOT / "supabase/tests").glob("*.sql")):
        execute(file.read_text())
    print("All migration and owner/role regression checks passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    run(parser.parse_args().url)
