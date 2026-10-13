--
-- PostgreSQL database dump
--

\restrict IkO8fsgtS0zwMdnvL4EKNrWOz26qXgWcvpNSw7dSeSQHWa630CMr9zLUnAP4f1b

-- Dumped from database version 17.6
-- Dumped by pg_dump version 17.11

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA public;


--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS 'standard public schema';


--
-- Name: biz_set_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.biz_set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$;


--
-- Name: reg_delete_document_embeddings(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reg_delete_document_embeddings(doc_id uuid) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM reg_embeddings WHERE document_id = doc_id;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$;


--
-- Name: reg_get_document_chunks(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reg_get_document_chunks(doc_id uuid) RETURNS TABLE(chunk_id uuid, content text, page_number integer, chunk_index integer)
    LANGUAGE sql
    AS $$
    SELECT id, content, page_number, chunk_index
    FROM reg_embeddings
    WHERE document_id = doc_id
    ORDER BY chunk_index;
$$;


--
-- Name: reg_match_embeddings(extensions.vector, uuid, uuid, uuid, integer, double precision); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reg_match_embeddings(query_embedding extensions.vector, match_subject_id uuid, match_class_id uuid, match_school_id uuid, match_count integer DEFAULT 5, match_threshold double precision DEFAULT 0.3) RETURNS TABLE(id uuid, content text, page_number integer, chunk_index integer, metadata jsonb, similarity double precision)
    LANGUAGE plpgsql
    SET search_path TO 'public', 'extensions'
    AS $$
begin
    perform set_config('hnsw.iterative_scan', 'strict_order', true);
    return query
    select e.id, e.content, e.page_number, e.chunk_index, e.metadata,
           1 - (e.embedding <=> query_embedding) as similarity
    from reg_embeddings e
    where e.subject_id = match_subject_id
      and e.class_id   = match_class_id
      and e.school_id  = match_school_id
      and 1 - (e.embedding <=> query_embedding) > match_threshold
    order by e.embedding <=> query_embedding
    limit match_count;
end;
$$;


--
-- Name: reg_match_embeddings_school(extensions.vector, uuid, integer, double precision); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reg_match_embeddings_school(query_embedding extensions.vector, match_school_id uuid, match_count integer DEFAULT 6, match_threshold double precision DEFAULT 0.15) RETURNS TABLE(id uuid, content text, page_number integer, chunk_index integer, metadata jsonb, similarity double precision)
    LANGUAGE plpgsql
    SET search_path TO 'public', 'extensions'
    AS $$
begin
    perform set_config('hnsw.iterative_scan', 'strict_order', true);
    return query
    select e.id, e.content, e.page_number, e.chunk_index, e.metadata,
           1 - (e.embedding <=> query_embedding) as similarity
    from reg_embeddings e
    where e.school_id = match_school_id
      and 1 - (e.embedding <=> query_embedding) > match_threshold
    order by e.embedding <=> query_embedding
    limit match_count;
end;
$$;


--
-- Name: reg_update_updated_at_column(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reg_update_updated_at_column() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: biz_customers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.biz_customers (
    id uuid DEFAULT extensions.uuid_generate_v4() NOT NULL,
    user_id uuid,
    name character varying(160) NOT NULL,
    phone character varying(30),
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: biz_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.biz_items (
    id uuid DEFAULT extensions.uuid_generate_v4() NOT NULL,
    user_id uuid,
    name character varying(160) NOT NULL,
    unit character varying(30),
    cost_price numeric(14,2),
    sell_price numeric(14,2),
    stock_qty numeric(14,3) DEFAULT 0,
    low_stock_threshold numeric(14,3) DEFAULT 0,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: biz_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.biz_messages (
    id uuid DEFAULT extensions.uuid_generate_v4() NOT NULL,
    user_id uuid,
    role character varying(12) NOT NULL,
    content text NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT biz_messages_role_check CHECK (((role)::text = ANY ((ARRAY['user'::character varying, 'assistant'::character varying])::text[])))
);


--
-- Name: biz_transactions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.biz_transactions (
    id uuid DEFAULT extensions.uuid_generate_v4() NOT NULL,
    user_id uuid,
    type character varying(20) NOT NULL,
    item_name character varying(200),
    item_id uuid,
    quantity numeric(14,3),
    unit character varying(30),
    amount numeric(14,2),
    credit_amount numeric(14,2) DEFAULT 0,
    unit_price numeric(14,2),
    cost_price numeric(14,2),
    customer_id uuid,
    customer_name character varying(160),
    direction character varying(4),
    source character varying(10) DEFAULT 'text'::character varying,
    raw_text text,
    message_id uuid,
    occurred_at timestamp with time zone DEFAULT now(),
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT biz_transactions_type_check CHECK (((type)::text = ANY ((ARRAY['sale'::character varying, 'restock'::character varying, 'credit'::character varying, 'payment'::character varying, 'expense'::character varying, 'other'::character varying])::text[])))
);


--
-- Name: biz_users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.biz_users (
    id uuid DEFAULT extensions.uuid_generate_v4() NOT NULL,
    phone_number character varying(30) NOT NULL,
    name character varying(120),
    shop_name character varying(160),
    language character varying(10) DEFAULT 'en'::character varying,
    currency character varying(10) DEFAULT 'KSh'::character varying,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: biz_wa_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.biz_wa_events (
    wamid text NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: challenges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.challenges (
    challengeid uuid DEFAULT gen_random_uuid() NOT NULL,
    userid uuid,
    subject text NOT NULL,
    struggle_area text NOT NULL,
    correct_alternative text,
    logic_trap text,
    impact text,
    teacher_manual text,
    technical_gap text,
    socratic_hint text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    unit integer,
    topic text,
    subtopic text
);


--
-- Name: curriculum; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.curriculum (
    curriculumid uuid DEFAULT gen_random_uuid() NOT NULL,
    curriculumname text NOT NULL
);


--
-- Name: documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.documents (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    subject text,
    year integer,
    paper_number text,
    curriculum text,
    level text,
    url text,
    "timestamp" timestamp with time zone DEFAULT now(),
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.messages (
    messageid uuid DEFAULT gen_random_uuid() NOT NULL,
    userid uuid,
    message text,
    role text,
    "timestamp" timestamp with time zone DEFAULT now(),
    processed boolean DEFAULT false,
    conversation_id uuid,
    processed_at timestamp with time zone
);


--
-- Name: package; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.package (
    packageid uuid DEFAULT gen_random_uuid() NOT NULL,
    packagename text NOT NULL,
    usercapacity integer,
    packageprice numeric(10,2),
    messagecapacity integer
);


--
-- Name: practice_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.practice_sessions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    userid uuid NOT NULL,
    subject text NOT NULL,
    topics jsonb DEFAULT '[]'::jsonb NOT NULL,
    class_level text DEFAULT 'a-level'::text NOT NULL,
    difficulty text DEFAULT 'mixed'::text NOT NULL,
    questions jsonb DEFAULT '[]'::jsonb NOT NULL,
    answers jsonb DEFAULT '{}'::jsonb NOT NULL,
    score numeric(5,2),
    status text DEFAULT 'in_progress'::text NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    CONSTRAINT practice_sessions_difficulty_check CHECK ((difficulty = ANY (ARRAY['easy'::text, 'medium'::text, 'hard'::text, 'mixed'::text]))),
    CONSTRAINT practice_sessions_status_check CHECK ((status = ANY (ARRAY['in_progress'::text, 'completed'::text, 'abandoned'::text])))
);


--
-- Name: reg_assignment_submissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reg_assignment_submissions (
    id uuid DEFAULT extensions.uuid_generate_v4() NOT NULL,
    assignment_id uuid,
    student_id uuid,
    content text,
    grade character varying(50),
    feedback text,
    status character varying(20) DEFAULT 'submitted'::character varying,
    submitted_at timestamp with time zone DEFAULT now(),
    graded_at timestamp with time zone
);


--
-- Name: reg_assignment_targets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reg_assignment_targets (
    id uuid DEFAULT extensions.uuid_generate_v4() NOT NULL,
    assignment_id uuid,
    student_id uuid,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: reg_assignments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reg_assignments (
    id uuid DEFAULT extensions.uuid_generate_v4() NOT NULL,
    school_id uuid,
    teacher_id uuid,
    subject_id uuid,
    class_id uuid,
    title character varying(200) NOT NULL,
    description text,
    due_date timestamp with time zone,
    is_active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: reg_classes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reg_classes (
    id uuid DEFAULT extensions.uuid_generate_v4() NOT NULL,
    name character varying(100) NOT NULL,
    subject_id uuid,
    description text,
    is_active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: reg_documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reg_documents (
    id uuid DEFAULT extensions.uuid_generate_v4() NOT NULL,
    school_id uuid,
    subject_id uuid,
    class_id uuid,
    filename character varying(255) NOT NULL,
    file_path character varying(500) NOT NULL,
    doc_type character varying(50) DEFAULT 'notes'::character varying,
    chunk_count integer DEFAULT 0,
    is_indexed boolean DEFAULT false,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: reg_embeddings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reg_embeddings (
    id uuid DEFAULT extensions.uuid_generate_v4() NOT NULL,
    document_id uuid,
    school_id uuid,
    subject_id uuid,
    class_id uuid,
    content text NOT NULL,
    embedding extensions.vector(1536),
    page_number integer,
    chunk_index integer,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: reg_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reg_messages (
    id uuid DEFAULT extensions.uuid_generate_v4() NOT NULL,
    user_id uuid,
    school_id uuid,
    subject_id uuid,
    class_id uuid,
    role character varying(20) NOT NULL,
    content text NOT NULL,
    intent character varying(50),
    metadata jsonb DEFAULT '{}'::jsonb,
    conversation_id uuid,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT reg_messages_role_check CHECK (((role)::text = ANY ((ARRAY['user'::character varying, 'assistant'::character varying])::text[])))
);


--
-- Name: reg_schools; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reg_schools (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    slug text NOT NULL,
    is_active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: reg_subjects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reg_subjects (
    id uuid DEFAULT extensions.uuid_generate_v4() NOT NULL,
    name character varying(100) NOT NULL,
    slug character varying(50) NOT NULL,
    prompt_template text,
    retrieval_k integer DEFAULT 5,
    is_active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: reg_users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reg_users (
    id uuid DEFAULT extensions.uuid_generate_v4() NOT NULL,
    phone_number character varying(20) NOT NULL,
    display_name character varying(100),
    school_id uuid,
    role text DEFAULT 'student'::text NOT NULL,
    enrolled_by uuid,
    is_active boolean DEFAULT true,
    current_subject_id uuid,
    current_class_id uuid,
    preferences jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT reg_users_role_check CHECK ((role = ANY (ARRAY['school_admin'::text, 'teacher'::text, 'student'::text])))
);


--
-- Name: school; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.school (
    schoolid uuid DEFAULT gen_random_uuid() NOT NULL,
    schoolname text NOT NULL,
    address text,
    packageid uuid
);


--
-- Name: schooldocuments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schooldocuments (
    documentid uuid DEFAULT gen_random_uuid() NOT NULL,
    schoolid uuid,
    subjectid uuid,
    documentname text NOT NULL,
    documentvisibility boolean DEFAULT true
);


--
-- Name: subjects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.subjects (
    subjectid uuid DEFAULT gen_random_uuid() NOT NULL,
    subjectname text NOT NULL,
    curriculumid uuid,
    level text
);


--
-- Name: subscription; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.subscription (
    subscriptionid uuid DEFAULT gen_random_uuid() NOT NULL,
    subscriptiondate date DEFAULT CURRENT_DATE,
    subscriptionexpirydate date,
    packageid uuid
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    first_name text,
    last_name text,
    username text,
    phone_number text,
    school_name text,
    grade_level text,
    curriculum text,
    role text DEFAULT 'student'::text,
    schoolid uuid,
    is_active boolean DEFAULT true,
    tier smallint DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    signup_state text DEFAULT 'new'::text NOT NULL,
    state_data jsonb DEFAULT '{}'::jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT users_tier_check CHECK ((tier = ANY (ARRAY[1, 2, 3])))
);


--
-- Name: biz_customers biz_customers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.biz_customers
    ADD CONSTRAINT biz_customers_pkey PRIMARY KEY (id);


--
-- Name: biz_customers biz_customers_user_id_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.biz_customers
    ADD CONSTRAINT biz_customers_user_id_name_key UNIQUE (user_id, name);


--
-- Name: biz_items biz_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.biz_items
    ADD CONSTRAINT biz_items_pkey PRIMARY KEY (id);


--
-- Name: biz_items biz_items_user_id_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.biz_items
    ADD CONSTRAINT biz_items_user_id_name_key UNIQUE (user_id, name);


--
-- Name: biz_messages biz_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.biz_messages
    ADD CONSTRAINT biz_messages_pkey PRIMARY KEY (id);


--
-- Name: biz_transactions biz_transactions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.biz_transactions
    ADD CONSTRAINT biz_transactions_pkey PRIMARY KEY (id);


--
-- Name: biz_users biz_users_phone_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.biz_users
    ADD CONSTRAINT biz_users_phone_number_key UNIQUE (phone_number);


--
-- Name: biz_users biz_users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.biz_users
    ADD CONSTRAINT biz_users_pkey PRIMARY KEY (id);


--
-- Name: biz_wa_events biz_wa_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.biz_wa_events
    ADD CONSTRAINT biz_wa_events_pkey PRIMARY KEY (wamid);


--
-- Name: challenges challenges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.challenges
    ADD CONSTRAINT challenges_pkey PRIMARY KEY (challengeid);


--
-- Name: challenges challenges_userid_subject_struggle_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.challenges
    ADD CONSTRAINT challenges_userid_subject_struggle_key UNIQUE (userid, subject, struggle_area);


--
-- Name: curriculum curriculum_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.curriculum
    ADD CONSTRAINT curriculum_pkey PRIMARY KEY (curriculumid);


--
-- Name: documents documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (id);


--
-- Name: messages messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_pkey PRIMARY KEY (messageid);


--
-- Name: package package_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.package
    ADD CONSTRAINT package_pkey PRIMARY KEY (packageid);


--
-- Name: practice_sessions practice_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.practice_sessions
    ADD CONSTRAINT practice_sessions_pkey PRIMARY KEY (id);


--
-- Name: reg_assignment_submissions reg_assignment_submissions_assignment_id_student_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_assignment_submissions
    ADD CONSTRAINT reg_assignment_submissions_assignment_id_student_id_key UNIQUE (assignment_id, student_id);


--
-- Name: reg_assignment_submissions reg_assignment_submissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_assignment_submissions
    ADD CONSTRAINT reg_assignment_submissions_pkey PRIMARY KEY (id);


--
-- Name: reg_assignment_targets reg_assignment_targets_assignment_id_student_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_assignment_targets
    ADD CONSTRAINT reg_assignment_targets_assignment_id_student_id_key UNIQUE (assignment_id, student_id);


--
-- Name: reg_assignment_targets reg_assignment_targets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_assignment_targets
    ADD CONSTRAINT reg_assignment_targets_pkey PRIMARY KEY (id);


--
-- Name: reg_assignments reg_assignments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_assignments
    ADD CONSTRAINT reg_assignments_pkey PRIMARY KEY (id);


--
-- Name: reg_classes reg_classes_name_subject_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_classes
    ADD CONSTRAINT reg_classes_name_subject_id_key UNIQUE (name, subject_id);


--
-- Name: reg_classes reg_classes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_classes
    ADD CONSTRAINT reg_classes_pkey PRIMARY KEY (id);


--
-- Name: reg_documents reg_documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_documents
    ADD CONSTRAINT reg_documents_pkey PRIMARY KEY (id);


--
-- Name: reg_embeddings reg_embeddings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_embeddings
    ADD CONSTRAINT reg_embeddings_pkey PRIMARY KEY (id);


--
-- Name: reg_messages reg_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_messages
    ADD CONSTRAINT reg_messages_pkey PRIMARY KEY (id);


--
-- Name: reg_schools reg_schools_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_schools
    ADD CONSTRAINT reg_schools_pkey PRIMARY KEY (id);


--
-- Name: reg_schools reg_schools_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_schools
    ADD CONSTRAINT reg_schools_slug_key UNIQUE (slug);


--
-- Name: reg_subjects reg_subjects_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_subjects
    ADD CONSTRAINT reg_subjects_name_key UNIQUE (name);


--
-- Name: reg_subjects reg_subjects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_subjects
    ADD CONSTRAINT reg_subjects_pkey PRIMARY KEY (id);


--
-- Name: reg_subjects reg_subjects_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_subjects
    ADD CONSTRAINT reg_subjects_slug_key UNIQUE (slug);


--
-- Name: reg_users reg_users_phone_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_users
    ADD CONSTRAINT reg_users_phone_number_key UNIQUE (phone_number);


--
-- Name: reg_users reg_users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_users
    ADD CONSTRAINT reg_users_pkey PRIMARY KEY (id);


--
-- Name: school school_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.school
    ADD CONSTRAINT school_pkey PRIMARY KEY (schoolid);


--
-- Name: school school_schoolname_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.school
    ADD CONSTRAINT school_schoolname_key UNIQUE (schoolname);


--
-- Name: schooldocuments schooldocuments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schooldocuments
    ADD CONSTRAINT schooldocuments_pkey PRIMARY KEY (documentid);


--
-- Name: subjects subjects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subjects
    ADD CONSTRAINT subjects_pkey PRIMARY KEY (subjectid);


--
-- Name: subscription subscription_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscription
    ADD CONSTRAINT subscription_pkey PRIMARY KEY (subscriptionid);


--
-- Name: users users_phone_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_phone_number_key UNIQUE (phone_number);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: documents_lookup_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX documents_lookup_idx ON public.documents USING btree (subject, curriculum, paper_number, year, level);


--
-- Name: idx_biz_customers_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_biz_customers_user ON public.biz_customers USING btree (user_id);


--
-- Name: idx_biz_items_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_biz_items_user ON public.biz_items USING btree (user_id);


--
-- Name: idx_biz_messages_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_biz_messages_user ON public.biz_messages USING btree (user_id, created_at);


--
-- Name: idx_biz_tx_customer; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_biz_tx_customer ON public.biz_transactions USING btree (customer_id);


--
-- Name: idx_biz_tx_message; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_biz_tx_message ON public.biz_transactions USING btree (message_id);


--
-- Name: idx_biz_tx_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_biz_tx_type ON public.biz_transactions USING btree (user_id, type);


--
-- Name: idx_biz_tx_user_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_biz_tx_user_time ON public.biz_transactions USING btree (user_id, occurred_at DESC);


--
-- Name: idx_biz_users_phone; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_biz_users_phone ON public.biz_users USING btree (phone_number);


--
-- Name: idx_biz_wa_events_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_biz_wa_events_time ON public.biz_wa_events USING btree (created_at);


--
-- Name: idx_reg_assignment_targets_student; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reg_assignment_targets_student ON public.reg_assignment_targets USING btree (student_id);


--
-- Name: idx_reg_assignments_school; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reg_assignments_school ON public.reg_assignments USING btree (school_id);


--
-- Name: idx_reg_assignments_teacher; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reg_assignments_teacher ON public.reg_assignments USING btree (teacher_id);


--
-- Name: idx_reg_classes_subject; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reg_classes_subject ON public.reg_classes USING btree (subject_id);


--
-- Name: idx_reg_documents_school; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reg_documents_school ON public.reg_documents USING btree (school_id);


--
-- Name: idx_reg_documents_subject_class; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reg_documents_subject_class ON public.reg_documents USING btree (subject_id, class_id);


--
-- Name: idx_reg_embeddings_class_scope; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reg_embeddings_class_scope ON public.reg_embeddings USING btree (subject_id, class_id);


--
-- Name: idx_reg_embeddings_content_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reg_embeddings_content_trgm ON public.reg_embeddings USING gin (content public.gin_trgm_ops);


--
-- Name: idx_reg_embeddings_doc_chunk; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reg_embeddings_doc_chunk ON public.reg_embeddings USING btree (document_id, chunk_index);


--
-- Name: idx_reg_embeddings_school; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reg_embeddings_school ON public.reg_embeddings USING btree (school_id);


--
-- Name: idx_reg_embeddings_vector_hnsw; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reg_embeddings_vector_hnsw ON public.reg_embeddings USING hnsw (embedding extensions.vector_cosine_ops);


--
-- Name: idx_reg_messages_context; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reg_messages_context ON public.reg_messages USING btree (user_id, subject_id, class_id, created_at DESC);


--
-- Name: idx_reg_messages_conversation; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reg_messages_conversation ON public.reg_messages USING btree (user_id, conversation_id, created_at);


--
-- Name: idx_reg_messages_school; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reg_messages_school ON public.reg_messages USING btree (school_id);


--
-- Name: idx_reg_messages_user_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reg_messages_user_time ON public.reg_messages USING btree (user_id, created_at DESC);


--
-- Name: idx_reg_submissions_assignment; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reg_submissions_assignment ON public.reg_assignment_submissions USING btree (assignment_id);


--
-- Name: idx_reg_submissions_student; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reg_submissions_student ON public.reg_assignment_submissions USING btree (student_id);


--
-- Name: idx_reg_users_enrolled_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reg_users_enrolled_by ON public.reg_users USING btree (enrolled_by);


--
-- Name: idx_reg_users_phone; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reg_users_phone ON public.reg_users USING btree (phone_number);


--
-- Name: idx_reg_users_role; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reg_users_role ON public.reg_users USING btree (role);


--
-- Name: idx_reg_users_school; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reg_users_school ON public.reg_users USING btree (school_id);


--
-- Name: idx_reg_users_school_role; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reg_users_school_role ON public.reg_users USING btree (school_id, role);


--
-- Name: messages_conversation_timestamp_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX messages_conversation_timestamp_idx ON public.messages USING btree (conversation_id, "timestamp");


--
-- Name: messages_userid_role_timestamp_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX messages_userid_role_timestamp_idx ON public.messages USING btree (userid, role, "timestamp" DESC);


--
-- Name: practice_sessions_user_started_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX practice_sessions_user_started_idx ON public.practice_sessions USING btree (userid, started_at DESC);


--
-- Name: practice_sessions_user_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX practice_sessions_user_status_idx ON public.practice_sessions USING btree (userid, status);


--
-- Name: biz_customers trg_biz_customers_updated; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_biz_customers_updated BEFORE UPDATE ON public.biz_customers FOR EACH ROW EXECUTE FUNCTION public.biz_set_updated_at();


--
-- Name: biz_items trg_biz_items_updated; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_biz_items_updated BEFORE UPDATE ON public.biz_items FOR EACH ROW EXECUTE FUNCTION public.biz_set_updated_at();


--
-- Name: biz_users trg_biz_users_updated; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_biz_users_updated BEFORE UPDATE ON public.biz_users FOR EACH ROW EXECUTE FUNCTION public.biz_set_updated_at();


--
-- Name: reg_assignments update_reg_assignments_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_reg_assignments_updated_at BEFORE UPDATE ON public.reg_assignments FOR EACH ROW EXECUTE FUNCTION public.reg_update_updated_at_column();


--
-- Name: reg_classes update_reg_classes_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_reg_classes_updated_at BEFORE UPDATE ON public.reg_classes FOR EACH ROW EXECUTE FUNCTION public.reg_update_updated_at_column();


--
-- Name: reg_documents update_reg_documents_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_reg_documents_updated_at BEFORE UPDATE ON public.reg_documents FOR EACH ROW EXECUTE FUNCTION public.reg_update_updated_at_column();


--
-- Name: reg_subjects update_reg_subjects_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_reg_subjects_updated_at BEFORE UPDATE ON public.reg_subjects FOR EACH ROW EXECUTE FUNCTION public.reg_update_updated_at_column();


--
-- Name: reg_users update_reg_users_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_reg_users_updated_at BEFORE UPDATE ON public.reg_users FOR EACH ROW EXECUTE FUNCTION public.reg_update_updated_at_column();


--
-- Name: biz_customers biz_customers_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.biz_customers
    ADD CONSTRAINT biz_customers_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.biz_users(id) ON DELETE CASCADE;


--
-- Name: biz_items biz_items_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.biz_items
    ADD CONSTRAINT biz_items_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.biz_users(id) ON DELETE CASCADE;


--
-- Name: biz_messages biz_messages_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.biz_messages
    ADD CONSTRAINT biz_messages_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.biz_users(id) ON DELETE CASCADE;


--
-- Name: biz_transactions biz_transactions_customer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.biz_transactions
    ADD CONSTRAINT biz_transactions_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES public.biz_customers(id) ON DELETE SET NULL;


--
-- Name: biz_transactions biz_transactions_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.biz_transactions
    ADD CONSTRAINT biz_transactions_item_id_fkey FOREIGN KEY (item_id) REFERENCES public.biz_items(id) ON DELETE SET NULL;


--
-- Name: biz_transactions biz_transactions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.biz_transactions
    ADD CONSTRAINT biz_transactions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.biz_users(id) ON DELETE CASCADE;


--
-- Name: challenges challenges_userid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.challenges
    ADD CONSTRAINT challenges_userid_fkey FOREIGN KEY (userid) REFERENCES public.users(id);


--
-- Name: reg_assignment_submissions reg_assignment_submissions_assignment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_assignment_submissions
    ADD CONSTRAINT reg_assignment_submissions_assignment_id_fkey FOREIGN KEY (assignment_id) REFERENCES public.reg_assignments(id) ON DELETE CASCADE;


--
-- Name: reg_assignment_submissions reg_assignment_submissions_student_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_assignment_submissions
    ADD CONSTRAINT reg_assignment_submissions_student_id_fkey FOREIGN KEY (student_id) REFERENCES public.reg_users(id) ON DELETE CASCADE;


--
-- Name: reg_assignment_targets reg_assignment_targets_assignment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_assignment_targets
    ADD CONSTRAINT reg_assignment_targets_assignment_id_fkey FOREIGN KEY (assignment_id) REFERENCES public.reg_assignments(id) ON DELETE CASCADE;


--
-- Name: reg_assignment_targets reg_assignment_targets_student_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_assignment_targets
    ADD CONSTRAINT reg_assignment_targets_student_id_fkey FOREIGN KEY (student_id) REFERENCES public.reg_users(id) ON DELETE CASCADE;


--
-- Name: reg_assignments reg_assignments_class_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_assignments
    ADD CONSTRAINT reg_assignments_class_id_fkey FOREIGN KEY (class_id) REFERENCES public.reg_classes(id) ON DELETE SET NULL;


--
-- Name: reg_assignments reg_assignments_school_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_assignments
    ADD CONSTRAINT reg_assignments_school_id_fkey FOREIGN KEY (school_id) REFERENCES public.reg_schools(id) ON DELETE CASCADE;


--
-- Name: reg_assignments reg_assignments_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_assignments
    ADD CONSTRAINT reg_assignments_subject_id_fkey FOREIGN KEY (subject_id) REFERENCES public.reg_subjects(id) ON DELETE SET NULL;


--
-- Name: reg_assignments reg_assignments_teacher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_assignments
    ADD CONSTRAINT reg_assignments_teacher_id_fkey FOREIGN KEY (teacher_id) REFERENCES public.reg_users(id) ON DELETE CASCADE;


--
-- Name: reg_classes reg_classes_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_classes
    ADD CONSTRAINT reg_classes_subject_id_fkey FOREIGN KEY (subject_id) REFERENCES public.reg_subjects(id) ON DELETE CASCADE;


--
-- Name: reg_documents reg_documents_class_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_documents
    ADD CONSTRAINT reg_documents_class_id_fkey FOREIGN KEY (class_id) REFERENCES public.reg_classes(id) ON DELETE CASCADE;


--
-- Name: reg_documents reg_documents_school_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_documents
    ADD CONSTRAINT reg_documents_school_id_fkey FOREIGN KEY (school_id) REFERENCES public.reg_schools(id);


--
-- Name: reg_documents reg_documents_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_documents
    ADD CONSTRAINT reg_documents_subject_id_fkey FOREIGN KEY (subject_id) REFERENCES public.reg_subjects(id) ON DELETE CASCADE;


--
-- Name: reg_embeddings reg_embeddings_class_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_embeddings
    ADD CONSTRAINT reg_embeddings_class_id_fkey FOREIGN KEY (class_id) REFERENCES public.reg_classes(id) ON DELETE CASCADE;


--
-- Name: reg_embeddings reg_embeddings_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_embeddings
    ADD CONSTRAINT reg_embeddings_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.reg_documents(id) ON DELETE CASCADE;


--
-- Name: reg_embeddings reg_embeddings_school_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_embeddings
    ADD CONSTRAINT reg_embeddings_school_id_fkey FOREIGN KEY (school_id) REFERENCES public.reg_schools(id);


--
-- Name: reg_embeddings reg_embeddings_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_embeddings
    ADD CONSTRAINT reg_embeddings_subject_id_fkey FOREIGN KEY (subject_id) REFERENCES public.reg_subjects(id) ON DELETE CASCADE;


--
-- Name: reg_messages reg_messages_class_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_messages
    ADD CONSTRAINT reg_messages_class_id_fkey FOREIGN KEY (class_id) REFERENCES public.reg_classes(id);


--
-- Name: reg_messages reg_messages_school_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_messages
    ADD CONSTRAINT reg_messages_school_id_fkey FOREIGN KEY (school_id) REFERENCES public.reg_schools(id);


--
-- Name: reg_messages reg_messages_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_messages
    ADD CONSTRAINT reg_messages_subject_id_fkey FOREIGN KEY (subject_id) REFERENCES public.reg_subjects(id);


--
-- Name: reg_messages reg_messages_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_messages
    ADD CONSTRAINT reg_messages_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.reg_users(id) ON DELETE CASCADE;


--
-- Name: reg_users reg_users_current_class_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_users
    ADD CONSTRAINT reg_users_current_class_id_fkey FOREIGN KEY (current_class_id) REFERENCES public.reg_classes(id);


--
-- Name: reg_users reg_users_current_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_users
    ADD CONSTRAINT reg_users_current_subject_id_fkey FOREIGN KEY (current_subject_id) REFERENCES public.reg_subjects(id);


--
-- Name: reg_users reg_users_enrolled_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_users
    ADD CONSTRAINT reg_users_enrolled_by_fkey FOREIGN KEY (enrolled_by) REFERENCES public.reg_users(id) ON DELETE SET NULL;


--
-- Name: reg_users reg_users_school_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reg_users
    ADD CONSTRAINT reg_users_school_id_fkey FOREIGN KEY (school_id) REFERENCES public.reg_schools(id);


--
-- Name: school school_packageid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.school
    ADD CONSTRAINT school_packageid_fkey FOREIGN KEY (packageid) REFERENCES public.package(packageid) ON DELETE SET NULL;


--
-- Name: schooldocuments schooldocuments_schoolid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schooldocuments
    ADD CONSTRAINT schooldocuments_schoolid_fkey FOREIGN KEY (schoolid) REFERENCES public.school(schoolid) ON DELETE CASCADE;


--
-- Name: schooldocuments schooldocuments_subjectid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schooldocuments
    ADD CONSTRAINT schooldocuments_subjectid_fkey FOREIGN KEY (subjectid) REFERENCES public.subjects(subjectid) ON DELETE CASCADE;


--
-- Name: subjects subjects_curriculumid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subjects
    ADD CONSTRAINT subjects_curriculumid_fkey FOREIGN KEY (curriculumid) REFERENCES public.curriculum(curriculumid) ON DELETE CASCADE;


--
-- Name: subscription subscription_packageid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscription
    ADD CONSTRAINT subscription_packageid_fkey FOREIGN KEY (packageid) REFERENCES public.package(packageid) ON DELETE CASCADE;


--
-- Name: biz_customers; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.biz_customers ENABLE ROW LEVEL SECURITY;

--
-- Name: biz_items; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.biz_items ENABLE ROW LEVEL SECURITY;

--
-- Name: biz_messages; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.biz_messages ENABLE ROW LEVEL SECURITY;

--
-- Name: biz_transactions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.biz_transactions ENABLE ROW LEVEL SECURITY;

--
-- Name: biz_users; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.biz_users ENABLE ROW LEVEL SECURITY;

--
-- Name: biz_wa_events; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.biz_wa_events ENABLE ROW LEVEL SECURITY;

--
-- Name: challenges; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.challenges ENABLE ROW LEVEL SECURITY;

--
-- Name: curriculum; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.curriculum ENABLE ROW LEVEL SECURITY;

--
-- Name: documents; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.documents ENABLE ROW LEVEL SECURITY;

--
-- Name: messages; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.messages ENABLE ROW LEVEL SECURITY;

--
-- Name: package; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.package ENABLE ROW LEVEL SECURITY;

--
-- Name: practice_sessions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.practice_sessions ENABLE ROW LEVEL SECURITY;

--
-- Name: reg_assignment_submissions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.reg_assignment_submissions ENABLE ROW LEVEL SECURITY;

--
-- Name: reg_assignment_targets; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.reg_assignment_targets ENABLE ROW LEVEL SECURITY;

--
-- Name: reg_assignments; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.reg_assignments ENABLE ROW LEVEL SECURITY;

--
-- Name: reg_classes; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.reg_classes ENABLE ROW LEVEL SECURITY;

--
-- Name: reg_documents; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.reg_documents ENABLE ROW LEVEL SECURITY;

--
-- Name: reg_embeddings; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.reg_embeddings ENABLE ROW LEVEL SECURITY;

--
-- Name: reg_messages; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.reg_messages ENABLE ROW LEVEL SECURITY;

--
-- Name: reg_schools; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.reg_schools ENABLE ROW LEVEL SECURITY;

--
-- Name: reg_subjects; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.reg_subjects ENABLE ROW LEVEL SECURITY;

--
-- Name: reg_users; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.reg_users ENABLE ROW LEVEL SECURITY;

--
-- Name: school; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.school ENABLE ROW LEVEL SECURITY;

--
-- Name: schooldocuments; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.schooldocuments ENABLE ROW LEVEL SECURITY;

--
-- Name: subjects; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.subjects ENABLE ROW LEVEL SECURITY;

--
-- Name: subscription; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.subscription ENABLE ROW LEVEL SECURITY;

--
-- Name: users; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

--
-- PostgreSQL database dump complete
--

\unrestrict IkO8fsgtS0zwMdnvL4EKNrWOz26qXgWcvpNSw7dSeSQHWa630CMr9zLUnAP4f1b

