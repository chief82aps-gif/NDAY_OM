import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.src.routes import uploads, auth, audit, enhanced_audit, weekly_audit, weekly_audit_upload
from api.src.database import Base, engine

app = FastAPI()

# Create all tables on startup
@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)

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
        "http://localhost:3001",
        "http://127.0.0.1:3001",
        "http://localhost:8001",
        "http://127.0.0.1:8001",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(uploads.router, prefix="/upload")
app.include_router(auth.router, prefix="/auth")
app.include_router(audit.router, prefix="/audit")
app.include_router(enhanced_audit.router)
app.include_router(weekly_audit.router)
app.include_router(weekly_audit_upload.router)

@app.get("/")
def root():
    return {"message": "NDAY_OM API is running."}
