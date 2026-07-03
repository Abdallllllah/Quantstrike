from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel
from app.gateway.database import supabase
import logging

router = APIRouter(prefix="/api/auth", tags=["Authentication"])
logger = logging.getLogger("api_logger")

# Schema for incoming registration data
class UserRegisterSchema(BaseModel):
    first_name: str
    last_name: str
    school_name: str
    grade_level: str
    curriculum: str
    phone_number: str
    username: str

@router.post("/register")
async def register_user(user_data: UserRegisterSchema):
    try:
        # Convert to dict and insert into 'users' table
        result = supabase.table("users").insert(user_data.model_dump()).execute()
        
        return {
            "status": "success",
            "message": "User registered successfully",
            "data": result.data[0]
        }
    except Exception as e:
        logger.error(f"Registration failed: {str(e)}")
        # Usually happens if phone_number or username already exists (Unique constraint)
        raise HTTPException(status_code=400, detail="Registration failed. User may already exist.")

@router.post("/login")
async def login_user(
    phone_number: str = Body(..., embed=True),
    username: str = Body(..., embed=True),
):
    """
    Sign-in accepts the phone number paired with ANY of:
      - username
      - first_name
      - last_name
    Matched case-insensitively. The frontend keeps sending the chosen value
    in the `username` field, regardless of which one the student typed.
    """
    try:
        # Pull every user with this phone number first — there's only ever a
        # handful, since phone_number is the primary login key. Then match the
        # identifier against any of the three name columns in Python so we
        # don't have to wrangle PostgREST's OR + ILIKE escaping.
        result = (
            supabase.table("users")
            .select("*")
            .eq("phone_number", phone_number)
            .execute()
        )

        if not result.data:
            raise HTTPException(status_code=401, detail="Invalid login details")

        ident = (username or "").strip().lower()
        if not ident:
            raise HTTPException(status_code=401, detail="Invalid login details")

        match = None
        for u in result.data:
            for col in ("username", "first_name", "last_name"):
                value = (u.get(col) or "").strip().lower()
                if value and value == ident:
                    match = u
                    break
            if match:
                break

        if not match:
            raise HTTPException(status_code=401, detail="Invalid login details")

        return {
            "status": "success",
            "message": "Login successful",
            "user": match,
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Login error: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")