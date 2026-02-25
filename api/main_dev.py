"""
Development version of main.py with PostgreSQL support
Run this locally for testing - production uses main.py unchanged
"""

import os
import sys
from pathlib import Path

# Add parent directory to path for imports
parent_dir = Path(__file__).parent.parent
sys.path.insert(0, str(parent_dir))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.src.routes import uploads, auth
from api.src.database import Base, engine

app = FastAPI(
    title="NDAY_OM - Development",
    version="2.0.0-dev",
    description="Local development version with PostgreSQL"
)

@app.on_event("startup")
async def startup_event():
    """Initialize database on startup"""
    print("\n" + "="*60)
    print("🚀 NDAY Route Manager - DEVELOPMENT MODE")
    print("="*60)
    print("Database: PostgreSQL (local)")
    print("API: http://127.0.0.1:8000")
    print("="*60)
    print("⚙️  Initializing database...")
    Base.metadata.create_all(bind=engine)
    print("✓ Database tables ready\n")

@app.on_event("shutdown")
async def shutdown_event():
    print("\n✓ Development server stopped\n")

# CORS configuration (same as production)
cors_origins_env = os.getenv("CORS_ORIGINS", "").strip()
if cors_origins_env:
    cors_origins = [origin.strip() for origin in cors_origins_env.split(",") if origin.strip()]
else:
    cors_origins = [
        "https://www.newdaylogisticsllc.com",
        "https://newdaylogisticsllc.com",
        "https://nday-om.vercel.app",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5000",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include existing routes (unchanged)
app.include_router(uploads.router, prefix="/upload")
app.include_router(auth.router, prefix="/auth")

@app.get("/")
def root():
    return {
        "message": "NDAY_OM API is running (DEVELOPMENT MODE)",
        "database": "PostgreSQL (local)",
        "environment": "development"
    }

# Development-only endpoints
@app.get("/dev/health")
async def dev_health():
    """Development health check"""
    from api.src.database import SessionLocal, User, Assignment
    db = SessionLocal()
    try:
        user_count = db.query(User).count()
        assignment_count = db.query(Assignment).count()
        return {
            "status": "ok",
            "environment": "development",
            "database": "postgresql (local)",
            "stats": {
                "users": user_count,
                "assignments": assignment_count
            }
        }
    finally:
        db.close()

@app.post("/dev/database/reset")
async def dev_database_reset():
    """⚠️ DANGER: Reset development database (all data will be lost)"""
    print("⚠️  Resetting development database...")
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    print("✓ Database reset complete")
    return {
        "message": "Development database reset successfully",
        "warning": "All data has been deleted"
    }

if __name__ == "__main__":
    # Load environment variables
    from dotenv import load_dotenv
    from pathlib import Path
    env_file = Path(__file__).parent / '.env.development'
    load_dotenv(env_file)
    
    import uvicorn
    uvicorn.run(
        "api.main_dev:app",
        host="127.0.0.1",
        port=8000,
        reload=True
    )
