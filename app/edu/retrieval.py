"""
Knowledge-base retrieval for grounded answers (LLM-1).

Hybrid: an exact-phrase keyword search for definition questions, then vector
similarity; each hit is widened with its neighbouring chunks and stitched back
together so the model sees whole passages verbatim.
"""
import re
from typing import Optional

from app.db.supabase import get_supabase_client


_DEF_PREFIX = re.compile(
    r"^\s*(?:please\s+)?(?:what\s+(?:is|are|was|were|do\s+you\s+mean\s+by|does)|define|definition\s+of|"
    r"meaning\s+of|state\s+(?:the\s+)?(?:definition\s+of|meaning\s+of)?|explain|describe|"
    r"what's|whats|give\s+(?:the\s+)?definition\s+of)\s+",
    re.IGNORECASE,
)
_DEF_TRAIL = re.compile(r"\s*(?:\?|\.|!|\bmean\b|\bmeans\b|\bin\s+\w+(?:\s+\w+)?)*\s*$", re.IGNORECASE)
_ARTICLE = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)


def _definition_term(query: str) -> Optional[str]:
    """Pull the term out of 'what is X?' / 'define X' style questions, or None."""
    q = query.strip()
    m = _DEF_PREFIX.match(q)
    if not m:
        return None
    term = _ARTICLE.sub("", _DEF_TRAIL.sub("", q[m.end():])).strip(" ?.!,:;\"'")
    return term if 2 < len(term) <= 60 and "%" not in term else None


def _raw_chunk(row: dict) -> str:
    """The chunk's original text, without the 'From: … | Page N' embedding prefix."""
    meta = row.get("metadata") or {}
    raw = meta.get("raw_content")
    if raw:
        return raw
    content = row.get("content") or ""
    if content.startswith("From: ") and "\n\n" in content:
        return content.split("\n\n", 1)[1]
    return content


def _stitch(rows: list) -> str:
    """Join consecutive chunks of one document back into continuous text,
    trimming the splitter's overlap so a definition cut across two chunks is
    read whole and verbatim."""
    out = ""
    prev = None
    for r in rows:
        text = _raw_chunk(r)
        meta = r.get("metadata") or {}
        pmeta = (prev or {}).get("metadata") or {}
        if prev is None:
            out = text
        elif (r.get("page_number") == prev.get("page_number")
              and meta.get("start_index") is not None and pmeta.get("start_index") is not None):
            overlap = pmeta["start_index"] + len(_raw_chunk(prev)) - meta["start_index"]
            out += text[overlap:] if 0 <= overlap < len(text) else "\n" + text
        else:
            out += "\n" + text
        prev = r
    return out.strip()


def _keyword_hits(db, school_id, term: str, limit: int = 20) -> list:
    """Exact-phrase lookup, ranked so chunks that actually DEFINE the term
    ('X is …', 'X refers to …', '… is called X') come first."""
    rows = (
        db.table("reg_embeddings")
        .select("id,document_id,content,page_number,chunk_index,metadata")
        .eq("school_id", str(school_id)).ilike("content", f"%{term}%")
        .limit(limit * 2).execute().data
    ) or []
    t = re.escape(term)
    defines = re.compile(
        rf"\b{t}\b[^.\n]{{0,25}}?\b(?:is|are|refers?\s+to|means?|is\s+defined\s+as|can\s+be\s+defined\s+as)\b"
        rf"|\bdefined?\s+(?:as\s+)?{t}\b|\b(?:is|are)\s+(?:called|known\s+as|termed)\s+(?:an?\s+|the\s+)?{t}\b"
        rf"|^\s*{t}\s*[:\-–—]",
        re.IGNORECASE | re.MULTILINE,
    )
    word = re.compile(rf"\b{t}\b", re.IGNORECASE)

    def score(r):
        raw = _raw_chunk(r)
        return 2 if defines.search(raw) else 1 if word.search(raw) else 0

    return sorted((r for r in rows if score(r)), key=score, reverse=True)


async def retrieve_context(school_id, query: str) -> str:
    """Subject-agnostic knowledge-base lookup across the school's uploads.

    Hybrid retrieval: for definition questions an exact-phrase keyword search
    runs first (embeddings alone often rank a passing mention above the actual
    definition), then vector similarity fills in. Each hit is widened with its
    neighbouring chunks and stitched back together, so the model sees the
    whole passage word-for-word rather than a 300-char fragment it would
    otherwise complete from memory. Returns '' (LLM-only) on failure."""
    if not school_id or not query or not query.strip():
        return ""
    try:
        import asyncio
        from app.rag_legacy import get_embeddings
        client = get_supabase_client()
        db = client.client

        hits: list = []
        term = _definition_term(query)
        if term:
            hits.extend(await asyncio.to_thread(_keyword_hits, db, school_id, term))

        emb = get_embeddings()
        vector = await asyncio.to_thread(emb.embed_query, query.strip())
        for r in client.similarity_search_school(vector, school_id, limit=8, threshold=0.15):
            meta = r.get("metadata") or {}
            r.setdefault("document_id", meta.get("document_id"))
            hits.append(r)

        # De-dupe, keep rank order, cap the number of distinct passages.
        seen, ranked = set(), []
        for r in hits:
            key = (r.get("document_id"), r.get("chunk_index"))
            if key not in seen and r.get("chunk_index") is not None:
                seen.add(key)
                ranked.append(r)
        ranked = ranked[:6]
        if not ranked:
            return ""

        # Widen each hit with the chunk before and after it.
        wanted: dict = {}
        for r in ranked:
            i = r["chunk_index"]
            wanted.setdefault(r.get("document_id"), set()).update({i - 1, i, i + 1})
        by_doc: dict = {}
        for doc_id, idxs in wanted.items():
            if not doc_id:
                continue
            rows = (
                db.table("reg_embeddings")
                .select("document_id,content,page_number,chunk_index,metadata")
                .eq("document_id", str(doc_id)).in_("chunk_index", sorted(i for i in idxs if i >= 0))
                .execute().data
            ) or []
            by_doc[doc_id] = {row["chunk_index"]: row for row in rows}

        # Build one passage per contiguous run, in the order of the best hit.
        passages, used = [], set()
        for r in ranked:
            doc_id, i = r.get("document_id"), r["chunk_index"]
            chunks = by_doc.get(doc_id) or {i: r}
            if (doc_id, i) in used:
                continue
            lo = i
            while lo - 1 in chunks and (doc_id, lo - 1) not in used:
                lo -= 1
            hi = i
            while hi + 1 in chunks and (doc_id, hi + 1) not in used:
                hi += 1
            run = [chunks[k] for k in range(lo, hi + 1) if k in chunks]
            used.update((doc_id, k) for k in range(lo, hi + 1))
            source = (run[0].get("content") or "").split("\n\n", 1)[0]
            source = source if source.startswith("From: ") else ""
            text = _stitch(run)
            if text:
                passages.append(f"[{source}]\n{text}" if source else text)

        return ("\n\n---\n\n".join(passages))[:9000]
    except Exception as e:  # noqa: BLE001
        print(f"KB retrieval skipped: {e}")
        return ""
