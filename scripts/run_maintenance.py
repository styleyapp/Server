"""Run the idempotent Task 3 retention/deletion worker with Server-only credentials."""

import asyncio
import json

import httpx

from app.core.config import Settings
from app.integrations.supabase.lifecycle import SupabaseLifecycle


async def run():
    async with httpx.AsyncClient() as client:
        async with asyncio.timeout(240):
            result = await SupabaseLifecycle(client, Settings.from_env()).maintain()
        print(json.dumps(result))


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except Exception:
        print("Maintenance failed; the durable jobs will be retried.")
        raise SystemExit(1) from None
