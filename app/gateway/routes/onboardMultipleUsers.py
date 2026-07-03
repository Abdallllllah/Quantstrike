from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from app.gateway.database import supabase
import pandas as pd
import io
import logging
router = APIRouter(prefix="/api/users", tags=["User Management"])
logger = logging.getLogger("api_logger")

@router.post("/upload-excel")
async def upload_users_from_excel(
    school_id: str = Form(...), 
    role: str = Form("student"),
    file: UploadFile = File(...)
):
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(status_code=400, detail="Please upload a valid Excel file.")

    try:
        contents = await file.read()
        df = pd.read_excel(io.BytesIO(contents))

        # 1. Standardize columns
        df.columns = [str(col).strip().lower().replace(" ", "_") for col in df.columns]

        # 2. Validation
        required_cols = ['first_name', 'last_name', 'phone_number']
        if not all(col in df.columns for col in required_cols):
            missing = [c for c in required_cols if c not in df.columns]
            raise HTTPException(status_code=400, detail=f"Missing columns: {missing}")

        # 3. Mapping with is_active logic
        users_to_insert = []
        for _, row in df.iterrows():
            # Check if Excel has an 'is_active' column, else default to True
            active_status = row.get('is_active')
            if active_status is None:
                is_active = True
            else:
                # Convert Excel truthy values (1, 'true', 'Yes') to Python bool
                is_active = str(active_status).lower() in ['true', '1', 'yes', 'active']

            user_data = {
                "first_name": str(row['first_name']),
                "last_name": str(row['last_name']),
                "phone_number": str(row['phone_number']),
                "school_name": str(row.get('school_name', '')),
                "grade_level": str(row.get('grade_level', '')),
                "curriculum": str(row.get('curriculum', '')),
                "username": str(row.get('username', f"{row['first_name']}.{row['last_name']}".lower())),
                "role": role,
                "schoolid": school_id,
                "is_active": is_active # New boolean field
            }
            users_to_insert.append(user_data)

        # 4. Insert (ID and CREATED_AT handled by Supabase)
        result = supabase.table("users").insert(users_to_insert).execute()

        return {
            "status": "success",
            "message": f"Successfully imported {len(users_to_insert)} users.",
            "data": result.data
        }

    except Exception as e:
        logger.error(f"Excel upload failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Server Error: {str(e)}")