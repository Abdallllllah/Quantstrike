"""Carati HTTP API. Every route lives under /api/edu."""
from app.edu.api.admin import router as admin_router
from app.edu.api.student import router as student_router
from app.edu.api.tracks import router as tracks_router

routers = (student_router, tracks_router, admin_router)
