# Database Context

Context file for AI coding assistants and developers working on this codebase.
The authoritative DDL is in `schema.sql` (exported from the live database). This file explains
what the schema means, how it is used, and the rules for changing it.

Never put real keys, passwords or connection strings in this file or in the repo. They live in
environment variables only.

---

## 1. Infrastructure

| Item | Value |
|---|---|
| Platform | Self-hosted Supabase on Railway (template `6ixfalls/supabase`) |
| Railway project / env | `zestful-intuition` / `production` (region: Southeast Asia) |
| Postgres | 17.6 (`ghcr.io/6ixfalls/supabase-postgres:17.6.1.136`) |
| API gateway (SUPABASE_URL) | `https://envoy-production-a1bf.up.railway.app` (Envoy, replaces Kong) |
| Studio dashboard | Same Envoy URL, HTTP basic auth (`DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` on the Envoy service) |
| Storage backend | Railway S3-compatible bucket |
| Private DB host (inside Railway only) | `postgres.railway.internal:5432` |
| Migrated from | Supabase Cloud project `bdnvaahnvqrkqredddsv` (eu-west-1) on 2026-10-13 |

Services in the Railway project: Postgres, Supavisor (pooler), Postgrest, Gotrue Auth, Supabase
Storage, Imgproxy, Supabase Realtime, Postgres Meta, Supabase Studio, Envoy, S3 bucket.

### Environment variables the app uses

```
SUPABASE_URL              = <Envoy URL above>
SUPABASE_KEY              = <service_role key>   # intentionally the service key, see Security
SUPABASE_SERVICE_ROLE_KEY = <service_role key>
OPENAI_API_KEY            = <...>
```

The app talks to the database through the Supabase client (PostgREST + RPC) and Storage API.
Direct Postgres connections are only for migrations and admin work.

### Database roles

- `postgres`: owner of all app tables. Not a superuser. Use it for migrations.
- `supabase_admin`: superuser. Only for operations that need it (`--disable-triggers` restores, etc.).
- `service_role`: what the backend authenticates as via the API. Bypasses RLS.
- `anon`, `authenticated`: have no access to app tables (revoked).

---

## 2. Extensions

| Extension | Schema | Used for |
|---|---|---|
| `vector` (pgvector) | `extensions` | Embeddings, HNSW index, `<=>` cosine distance |
| `pg_trgm` | search_path default | Trigram GIN index for ILIKE phrase search on chunk text |
| `uuid-ossp` | search_path default | `uuid_generate_v4()` defaults |

Because `vector` lives in the `extensions` schema, SQL functions that use `vector` or `<=>` must
set `search_path = public, extensions` (see section 6).

---

## 3. Domains and table groups

The `public` schema holds three products that share one database. Keep them isolated by prefix.

| Prefix / group | Product | Notes |
|---|---|---|
| `reg_*` | Edu app: multi-tenant school RAG tutor | Schools, users, subjects, classes, documents, embeddings, assignments, chat |
| `biz_*` | Tara: small-shop record keeping over WhatsApp | Isolated from the edu app |
| Gateway tables (`users`, `messages`, `documents`, `practice_sessions`, `challenges`) | WhatsApp tutor gateway: past papers, practice mode | `users`/`messages`/`documents` predate the repo migrations |
| Legacy tables (`package`, `school`, `curriculum`, `subjects`, `schooldocuments`, `subscription`) | Older tutor data model | Not created by the repo migrations; exist with data. See `schema.sql` for their exact columns |

---

## 4. Tables

Types abbreviated: `ts` = `timestamptz`. All `id` columns are UUID unless stated.

### 4.1 Edu app (`reg_*`)

**reg_schools**: tenant root.
`id`, `name` (not null), `slug` (unique, not null), `is_active` (default true), `created_at`, `updated_at`.

**reg_subjects**: `id`, `name` (unique), `slug` (unique), `prompt_template` (text, per-subject system prompt),
`retrieval_k` (int, default 5), `is_active`, `created_at`, `updated_at`. 10 rows (Mathematics, Physics, ...).

**reg_classes**: `id`, `name`, `subject_id` → reg_subjects (cascade), `description`, `is_active`,
timestamps. Unique `(name, subject_id)`.

**reg_users**: `id`, `phone_number` (unique, not null), `display_name`, `school_id` → reg_schools,
`role` (check: `school_admin` | `teacher` | `student`, default `student`), `enrolled_by` → reg_users
(self-reference, set null), `is_active`, `current_subject_id`, `current_class_id`, `preferences` (jsonb),
timestamps. The self-reference makes data-only restores need `--disable-triggers`.

**reg_messages**: chat history. `id`, `user_id` (cascade), `school_id`, `subject_id`, `class_id`,
`role` (check: `user` | `assistant`), `content`, `intent`, `metadata` (jsonb), `conversation_id`, `created_at`.

**reg_documents**: uploaded course material. `id`, `school_id`, `subject_id` (cascade), `class_id` (cascade),
`filename`, `file_path`, `doc_type` (default `notes`), `chunk_count`, `is_indexed`, `metadata`, timestamps.
- `file_path` is stored as `uploads/<filename>`, but the Storage object key is just `<filename>` at the
  root of the `reg_documents` bucket. Resolve files by `filename`, not `file_path`.
- Several rows can share one file (216 rows → 206 files at migration).

**reg_embeddings**: RAG chunks. `id`, `document_id` → reg_documents (cascade), `school_id`, `subject_id`,
`class_id`, `content` (text, not null), `embedding` `vector(1536)`, `page_number`, `chunk_index`,
`metadata`, `created_at`. 33,653 rows at migration.

**reg_assignments**: `id`, `school_id`, `teacher_id` → reg_users, `subject_id`, `class_id`, `title`,
`description`, `due_date`, `is_active`, timestamps.

**reg_assignment_targets**: `assignment_id`, `student_id`; unique `(assignment_id, student_id)`.

**reg_assignment_submissions**: `assignment_id`, `student_id`, `content`, `grade`, `feedback`,
`status` (`submitted` | `graded`), `submitted_at`, `graded_at`; unique `(assignment_id, student_id)`.

`updated_at` triggers (`reg_update_updated_at_column`) on subjects, classes, users, documents, assignments.

### 4.1b Carati v1 (migration `migrations/009_carati_v1.sql`, Requirements v0.2)

Not yet in `schema.sql` until it is re-exported after the migration runs.

**reg_users additions**: `level` (`lower_sixth` | `upper_sixth`), `subjects` text[] (subject slugs),
`school_name`, `region`, `exam_year`, `onboarded_at`, `last_seen_at`, `legacy_user_id` (the same person's
old gateway `users.id`). Role check now `student` | `teacher` | `admin` | `school_admin` (the last is legacy,
unused). Phones are stored normalised as `+<digits>` (`reg_normalize_phone()`; bare 9-digit Cameroon numbers
get `+237`). Students are identified by phone only: no names are collected.

One identity: the migration copied WhatsApp-gateway `users` into `reg_users` (phone match, no names),
linking them through `legacy_user_id`. The gateway code now resolves students in `reg_users`
(`app/gateway/identity.py`); its `messages` / `practice_sessions` rows written before the merge stay under the
legacy id and are read through both ids.

| Table | Purpose |
|---|---|
| `reg_otp_codes` | Hashed 6-digit login codes (phone, expiry, attempts). |
| `reg_syllabus_topics` | subject → topic → sub-topic (`parent_id`), `code`, `verified` (false = auto-created by tagging). |
| `reg_messages` (+cols) | `topic_id`, `subtopic_id`, `counts_against_allowance`, `allowance_source`, `cached`, `rating` (±1), `unsure`, `is_reexplanation`, `channel` (app/whatsapp), `client_ref` (idempotency, unique per user), `review_status`, `correction`. |
| `reg_answer_cache` | Cached answers by normalised-question hash; `disabled` after a thumbs-down; corrections overwrite it. |
| `reg_daily_usage` | (user, WAT day) free units used: `questions_used`, `papers_used`. |
| `reg_wallets` | Paid `question_credits`, `paper_credits`, `season_pass_until`. |
| `reg_usage_events` | One row per unit spent; unique (user, kind, day, ref) makes retries free. |
| `reg_past_papers` | Paper bank, one row per past-paper document (`years[]`, `paper_numbers[]`, `kind`). Filled by `papers_service.sync_papers()` from `reg_documents` filenames. |
| `reg_past_questions` | Questions extracted from a paper on first use (`number`, `text`, `marks`, `marking_scheme`, `topic_id`). |
| `reg_paper_unlocks` | (user, paper, WAT day) opens; `completed_at` once every question has an attempt. |
| `reg_attempts` | Marked answers: marks, `breakdown` jsonb (M/A/B points), `marked_by` llm/teacher/pending_teacher. |
| `reg_topic_stats` | Profile: per (user, topic) asked / attempted / marks / unsure / last activity. |
| `reg_teacher_profiles` | Teacher vetting (ID, credential, school, results evidence), `verification_status`, `revenue_share` 0.65–0.75, payout wallet. |
| `reg_tracks`, `reg_track_steps`, `reg_track_items` | Revision tracks (dated weekly steps, items: note/document/audio/worked_question/question). Files in private bucket `track_content`. |
| `reg_enrolments`, `reg_step_progress` | Paid enrolment (parent phone, payment), per-step completion. |
| `reg_track_posts` | Weekly group Q&A; `author_id` NULL = answered by Carati; one teacher `clarification` per week. |
| `reg_payments`, `reg_payouts` | FCFA ledger (track / question_pack / paper_pack / season_pass), teacher payouts. |

Functions (all `service_role` only):
- `reg_consume_allowance(user, kind, ref, subject, free_cap, raised_cap)`: the only place units are spent.
  Order: free daily unit, then season pass, then paid credit. Raised cap applies when the subject has an
  active track enrolment (`reg_has_raised_cap`). Per-user advisory lock, so no double spending.
- `reg_grant_payment(payment)`: marks paid and grants credits / pass / enrolment exactly once.
- `reg_bump_topic_stat(...)`: atomic profile counters. `reg_wat_today()`, `reg_normalize_phone()`.

Old school-app tables (`reg_assignments`, `reg_assignment_targets`, `reg_assignment_submissions`) are no
longer used by any code; they were left in place (no data dropped).

### 4.2 Tara (`biz_*`)

- **biz_users**: `phone_number` (unique), `name`, `shop_name`, `language` (default `en`), `currency` (default `KSh`).
- **biz_customers**: `user_id` → biz_users, `name`, `phone`; unique `(user_id, name)`.
- **biz_items**: `user_id`, `name`, `unit`, `cost_price`, `sell_price`, `stock_qty`, `low_stock_threshold`; unique `(user_id, name)`.
- **biz_transactions**: `user_id`, `type` (check: `sale` | `restock` | `credit` | `payment` | `expense` | `other`),
  `item_name`, `item_id`, `quantity`, `unit`, `amount`, `credit_amount`, `unit_price`, `cost_price`,
  `customer_id`, `customer_name`, `direction`, `source` (default `text`), `raw_text`, `message_id`,
  `occurred_at`, `metadata`, `created_at`.
- **biz_messages**: `user_id`, `role` (`user` | `assistant`), `content`, `created_at`.
- **biz_wa_events**: `wamid` (text PK), `created_at`. WhatsApp webhook de-duplication. Exists only in the new DB, currently empty.

`updated_at` triggers (`biz_set_updated_at`) on biz_users, biz_customers, biz_items.

### 4.3 Gateway (WhatsApp tutor)

**users**: `id`, `first_name`, `last_name`, `username`, `phone_number` (unique), `school_name`,
`grade_level`, `curriculum`, `role` (default `student`), `schoolid`, `is_active`,
`tier` (smallint 1–3, default 1), `created_at`, plus
`signup_state` (text, not null, default `'new'`), `state_data` (jsonb, not null, default `'{}'`),
`updated_at` (default now()). 49 rows at migration.

**messages**: `messageid` (PK), `userid`, `message`, `role` (`student` | `tutor`), `"timestamp"`,
`processed` (bool), `conversation_id`, `processed_at` (ts).

**documents**: past-paper index. `id`, `subject`, `year`, `paper_number`, `curriculum`, `level`, `url`,
`"timestamp"`, `created_at`. Empty at migration. Files would live in the `GCEResources` bucket.

**practice_sessions**: `userid`, `subject`, `topics` (jsonb array), `class_level` (default `a-level`),
`difficulty` (`easy` | `medium` | `hard` | `mixed`), `questions` (jsonb), `answers` (jsonb), `score`,
`status` (`in_progress` | `completed` | `abandoned`), `started_at`, `completed_at`.

**challenges**: per-student learning gaps. `challengeid` (PK), `userid` → users(id), `subject`,
`struggle_area`, `correct_alternative`, `logic_trap`, `impact`, `teacher_manual`, `technical_gap`,
`socratic_hint`, `unit`, `topic`, `subtopic`, timestamps. Unique `(userid, subject, struggle_area)`.

### 4.4 Legacy

- **package**: `packageid` (PK), `packagename`, `usercapacity`, `packageprice` numeric(10,2), `messagecapacity`.
- **school**: `schoolid` (PK), `schoolname` (unique), `address`, `packageid` → package (set null).
- **curriculum**: `curriculumid` (PK), `curriculumname`.
- **subjects**, **schooldocuments**, **subscription**: see `schema.sql`.

---

## 5. Vector search (RAG)

- Embedding model: OpenAI `text-embedding-3-large` with `dimensions=1536`.
  Every embeddings call (ingest and query) MUST pass `dimensions=1536`, or inserts and searches fail.
- Index: `idx_reg_embeddings_vector_hnsw`, HNSW with `vector_cosine_ops`. Similarity = `1 - (a <=> b)`.
- Other indexes on reg_embeddings: `(subject_id, class_id)`, `(school_id)`, `(document_id, chunk_index)`
  (neighbouring-chunk fetch), and GIN trigram on `content` (`idx_reg_embeddings_content_trgm`) for
  exact-phrase / definition ILIKE search.
- There must be no IVFFlat index on this table. An old `idx_reg_embeddings_vector` (ivfflat) was removed.

### RPC functions (call via `supabase.rpc(...)`)

```sql
reg_match_embeddings(
  query_embedding vector(1536), match_subject_id uuid, match_class_id uuid,
  match_school_id uuid, match_count int default 5, match_threshold float default 0.3)
  -> table(id uuid, content text, page_number int, chunk_index int, metadata jsonb, similarity float)

reg_match_embeddings_school(
  query_embedding vector(1536), match_school_id uuid,
  match_count int default 6, match_threshold float default 0.15)   -- used by the chat
  -> same return shape

reg_get_document_chunks(doc_id uuid)
  -> table(chunk_id uuid, content text, page_number int, chunk_index int)

reg_delete_document_embeddings(doc_id uuid) -> int (rows deleted)
```

Both match functions:
- are `plpgsql` with `SET search_path = public, extensions`;
- call `set_config('hnsw.iterative_scan', 'strict_order', true)` inside the body, so filtered
  searches (by school/subject/class) keep scanning until they fill `match_count`.
  Do not move this into `ALTER FUNCTION ... SET`: the non-superuser `postgres` role gets "permission denied".

---

## 6. Security model

- No RLS policies exist. RLS is enabled on every `public` table, so `anon`/`authenticated` see nothing.
- `service_role` (backend) bypasses RLS and has `ALL` on tables, sequences and functions.
- Both `SUPABASE_KEY` and `SUPABASE_SERVICE_ROLE_KEY` are the service key on purpose. The backend is the only client.
- The service key must never reach a browser, mobile app or WhatsApp message.

### Rules for every new table or function

Supabase's default privileges grant new tables to `anon`/`authenticated` automatically. So every
migration that creates a table must also:

```sql
alter table public.<t> enable row level security;
revoke all on public.<t> from anon, authenticated;
grant all on public.<t> to service_role;
```

and every new function:

```sql
revoke execute on function public.<fn>(<args>) from anon, authenticated;
grant execute on function public.<fn>(<args>) to service_role;
```

End each migration with `notify pgrst, 'reload schema';` so the API sees the change.

---

## 7. Storage

| Bucket | Public | Contents |
|---|---|---|
| `reg_documents` | No | 206 course PDFs, stored at bucket root by filename |
| `GCEResources` | Yes | Only `GCE/A-LEVEL/MATHEMATICS/.emptyFolderPlaceholder`. Intended for past papers served via `get_public_url()` |

Public URLs have the form `<SUPABASE_URL>/storage/v1/object/public/<bucket>/<path>`.
No database rows reference the old Supabase Cloud domain.

Known data gap: `reg_documents` row for `GCE_Physics_Paper1_June2020.pdf` (added 2026-09-21) has
no file in storage. It was already missing in the old project.

---

## 8. Writing migrations: conventions

- Idempotent: `create table if not exists`, `create index if not exists`, `add column if not exists`,
  `create or replace function`, `drop trigger if exists` before `create trigger`.
- Never drop tables in a migration without an explicit backup plan.
- Use `extensions.vector(1536)` and `extensions.vector_cosine_ops` (or set `search_path`).
- Do not re-run the old seed INSERTs for `reg_subjects` / `reg_classes`: real rows with their original
  IDs already exist, and seeds clash on `name`/`slug`.
- Building or rebuilding the HNSW index: Railway's container has only ~64 MB of shared memory, so
  parallel index builds fail with "No space left on device". Always:
  ```sql
  set max_parallel_maintenance_workers = 0;
  set maintenance_work_mem = '512MB';
  create index ... using hnsw (embedding extensions.vector_cosine_ops);
  ```
- Bulk loads into reg_embeddings: drop the HNSW and trigram indexes first, load, recreate them, then `analyze`.

---

## 9. Operational notes

- Windows `psql` defaults to WIN1252. Set `PGCLIENTENCODING=UTF8` before any COPY. The chunk text contains non-Latin characters.
- The public TCP proxy (if enabled) is unencrypted: the server does not support SSL. Use it only for one-off
  admin work and remove it afterwards. App services inside Railway should use `postgres.railway.internal`.
- Some office networks block DNS and large uploads to non-HTTP ports. Use a different network or the
  Railway service Console if a COPY drops with "incomplete message from client".
- Backups: self-hosted Supabase has no point-in-time recovery. Enable Railway volume backups on the
  Postgres service and schedule periodic `pg_dump`.

---

## 10. Snapshot at migration (2026-10-13)

| Object | Count |
|---|---|
| Tables in `public` | 26 (25 migrated + `biz_wa_events`) |
| users | 49 |
| reg_subjects | 10 |
| reg_documents | 216 |
| reg_embeddings | 33,653 |
| Storage files | 206 (`reg_documents`) + 1 placeholder (`GCEResources`) |

Row counts for every table matched the source database exactly after migration.
