# Quantstrike — one repo, one service

The three former projects are now a **single FastAPI app** deployed as one
Render service:

- **quantstrike** (RAG worker) — `app/` core: retrieval, `reg_*` tables, WhatsApp.
- **quantstrikeRetrieval / quantstrikeController** (gateway) — merged into
  `app/gateway/`: master chat, auth, practice, past-paper retrieval, vision/audio.
  *(The original `quantstrikeRetrieval/` folder was deleted after merging.)*

One ASGI app (`app.main:app`) serves everything; the master controller calls
this same app's `/api/rag`, `/api/extract`, `/api/messages/insert`, and
`/api/documents/retrieve` over localhost (`$BASE_URL` → `127.0.0.1:$PORT`).

## Layout

```
Quantstrike/
  app/
    main.py            # THE app — worker endpoints + gateway routers
    db/ rag/ controller/ whatsapp/   # worker internals (reg_*)
    gateway/
      database.py      # gateway Supabase (service-role) client
      routes/          # one router per file (12 routers)
      data/
  migrations/          # reg_* schema (000_full_setup.sql is canonical)
  sql/
    tier_migration.sql       # gateway: users.tier, messages.conversation_id, practice_sessions
    drop_caratitutor.sql     # destructive cleanup for removed LMS tables
  scripts/downloadgcepaps.py
  requirements.txt     # worker (pinned) + gateway deps, single UTF-8 file
  .env.example
```

## Run / deploy

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT   # one process, everything
```

Render: Build `pip install -r requirements.txt`, Start
`uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Set env vars from
[.env.example](.env.example).

## What was removed (redundant)

- **CaratiTutor LMS** — all 14 `routes/caratitutor/*` modules. Tables dropped
  via [sql/drop_caratitutor.sql](sql/drop_caratitutor.sql).
- **`rag_router.py`** — was an HTTP proxy to the externally-deployed worker.
  Redundant now that the worker's own `/api/rag` lives in the same app, so the
  single `/api/rag` (worker, `reg_*`) is canonical.
- **`addSchools` / `addClasses` / `addDocumentsToschools`** — duplicates that
  wrote to a *second* Supabase project (`ptcganlvwzwoljulnuso`). The worker's
  `reg_*` endpoints own that data now, and the second project is no longer used.
- The whole `quantstrikeRetrieval/` folder (137 MB of generated PDFs included).
- Unused `genai_client` (`google-genai` dependency avoided).

## ⚠️ Deploy-time checks

1. **One Supabase project.** `SUPABASE_URL` is shared. The worker's `reg_*`
   tables and the gateway's `users`/`messages`/`documents`/`practice_sessions`
   must be in that one project. If they're currently split, consolidate before
   deploying (see [.env.example](.env.example)).
2. **REDIS_URL is required** — `controller.py` builds a Redis client at import,
   so the app won't boot without it.
3. Frontend calls to the old gateway school/class/document endpoints must move
   to the worker routes: `POST /api/schools`, `POST /api/classes`,
   `POST /api/schools/{slug}/documents`.
4. The React app in `../caratitutor/` (separate Vercel frontend) was left
   untouched — prune any calls it makes to the removed LMS endpoints.

## Route map (37 operations, no collisions)

Worker: `/api/rag`, `/api/schools*`, `/api/classes*`, `/api/subjects`,
`/api/documents`, `/api/health`, `/api/ingest`, `/api/upload`, `/api/query`,
`/api/generate-questions`, `/api/mark-answer`, `/`.
Gateway: `/api/chat*`, `/api/auth/*`, `/api/practice/*`, `/api/extract`,
`/api/messages/insert`, `/api/users/upload-excel`, `/api/vision/*`,
`/api/audio/transcribe`, `/api/documents/retrieve`, `/api/documents/download`,
`/upload`.
