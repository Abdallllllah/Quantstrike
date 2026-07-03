import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

# Configuration — the gateway's Supabase project (service-role for backend writes).
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
STORAGE_BUCKET = os.getenv("SUPABASE_BUCKET")

# Shared Supabase client used by the gateway routes.
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
