"""Thin helpers over the Supabase (PostgREST) client for the Carati modules."""
import asyncio
from typing import Optional

from app.db.supabase import get_supabase_client


def db():
    return get_supabase_client().client


def one(query) -> Optional[dict]:
    rows = query.limit(1).execute().data
    return rows[0] if rows else None


def rpc(name: str, params: dict):
    return db().rpc(name, params).execute().data


async def run(fn, *args, **kwargs):
    """Run a blocking DB call off the event loop."""
    return await asyncio.to_thread(fn, *args, **kwargs)


_GENERAL_SCHOOL_ID: Optional[str] = None


def general_school_id() -> str:
    """The shared corpus school every student and document belongs to."""
    global _GENERAL_SCHOOL_ID
    if _GENERAL_SCHOOL_ID is None:
        row = one(db().table("reg_schools").select("id").eq("slug", "general"))
        if row is None:
            row = db().table("reg_schools").insert({"name": "General", "slug": "general"}).execute().data[0]
        _GENERAL_SCHOOL_ID = row["id"]
    return _GENERAL_SCHOOL_ID
