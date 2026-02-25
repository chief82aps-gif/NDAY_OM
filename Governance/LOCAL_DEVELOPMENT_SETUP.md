# Local Development Setup - PostgreSQL Testing (Offline)

**Purpose:** Develop and test database layer without affecting live website  
**Date:** February 23, 2026  
**Status:** Ready to implement

---

## Overview

This setup allows you to:
- ✅ Run a complete local PostgreSQL database on your computer
- ✅ Test all new features locally in VS Code
- ✅ Keep the current Render website completely untouched
- ✅ Deploy to production only after full testing
- ✅ Develop at your own pace without affecting users

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ YOUR LOCAL COMPUTER (Development)                           │
│                                                             │
│  ┌──────────────────┐         ┌───────────────────────┐   │
│  │ VS Code          │◄───────►│ Local PostgreSQL DB   │   │
│  │ (Backend code)   │         │ (testing.nday_om)     │   │
│  │                  │         │                       │   │
│  │ localhost:8000   │         │ port 5432             │   │
│  └──────────────────┘         └───────────────────────┘   │
│           ▲                                                 │
│           │ (Optional)                                     │
│           ▼                                                 │
│  ┌──────────────────┐                                      │
│  │ Postman/Insomnai │ Test API endpoints                  │
│  │ or curl          │                                      │
│  └──────────────────┘                                      │
└─────────────────────────────────────────────────────────────┘
                          ║
                          ║ (Keep Separate)
                          ║
┌─────────────────────────────────────────────────────────────┐
│ RENDER.COM (Production - UNCHANGED)                         │
│                                                             │
│  ┌──────────────────┐         ┌───────────────────────┐   │
│  │ Current Website  │◄───────►│ Current users.json    │   │
│  │ (Live)           │         │ (Live data)           │   │
│  │                  │         │                       │   │
│  │ nday-om.onrender │         │ In-memory storage     │   │
│  └──────────────────┘         └───────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## Step 1: Install Local PostgreSQL

### Windows
Download and install from: https://www.postgresql.org/download/windows/

**During installation:**
- Username: `postgres`
- Password: `postgres` (or choose your own)
- Port: `5432` (default)

Verify installation by opening PowerShell:
```powershell
psql --version
```

### macOS
```bash
brew install postgresql@15
brew services start postgresql@15
```

### Linux (Ubuntu/Debian)
```bash
sudo apt-get install postgresql postgresql-contrib
sudo service postgresql start
```

---

## Step 2: Create Local Test Database

Open PostgreSQL command line:

**Windows:**
```powershell
psql -U postgres
```

**macOS/Linux:**
```bash
psql -U postgres
```

Then run:

```sql
-- Create database for testing
CREATE DATABASE nday_om_dev;

-- Create user for testing (optional, but good practice)
CREATE USER nday_dev WITH PASSWORD 'dev_password_123';

-- Grant permissions
GRANT ALL PRIVILEGES ON DATABASE nday_om_dev TO nday_dev;

-- List databases (verify creation)
\l

-- Exit
\q
```

---

## Step 3: Create Development Configuration

Create `api/.env.development`:

```
# Development environment - LOCAL TESTING ONLY
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/nday_om_dev
ENVIRONMENT=development
DEBUG=True
API_HOST=127.0.0.1
API_PORT=8000
```

**IMPORTANT:** Add `.env.development` to `.gitignore` so it never commits:

```
# In .gitignore
.env
.env.development
.env.local
*.db
```

---

## Step 4: Install Backend Dependencies

```bash
cd api
pip install -r requirements.txt

# Add these if not already present:
pip install sqlalchemy==2.0.23
pip install psycopg2-binary==2.9.9
pip install python-dotenv==1.0.0
```

Update `api/requirements.txt`:

```
fastapi==0.104.1
uvicorn==0.24.0
python-multipart==0.0.6
python-jose==3.3.0
passlib==1.7.4
python-dotenv==1.0.0
sqlalchemy==2.0.23
psycopg2-binary==2.9.9
pandas==2.1.3
openpyxl==3.1.2
reportlab==4.0.7
requests==2.31.0
```

---

## Step 5: Update FastAPI Main for Development Mode

Create `api/main_dev.py` (separate from production):

```python
"""
Development version of main.py - uses local PostgreSQL
This file is for LOCAL TESTING ONLY
"""

import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from src.database import init_db, engine, Base

# Load development environment variables
load_dotenv('.env.development')

app = FastAPI(
    title="NDAY Route Manager - DEV",
    description="Local development version with PostgreSQL",
    version="2.0.0-dev"
)

# CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Events
@app.on_event("startup")
async def startup_event():
    """Initialize database on startup"""
    print("\n" + "="*60)
    print("🚀 NDAY Route Manager - DEVELOPMENT MODE")
    print("="*60)
    print(f"Environment: {os.getenv('ENVIRONMENT', 'development')}")
    print(f"Database: {os.getenv('DATABASE_URL')}")
    print(f"API: http://{os.getenv('API_HOST', '127.0.0.1')}:{os.getenv('API_PORT', 8000)}")
    print("="*60)
    print("⚙️  Initializing database...")
    
    # Create tables
    Base.metadata.create_all(bind=engine)
    print("✓ Database tables ready\n")

@app.on_event("shutdown")
async def shutdown_event():
    print("\n✓ Development server stopped\n")

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "ok",
        "environment": "development",
        "database": "postgresql (local)"
    }

# Include your existing routes (unchanged)
from src.routes import auth, uploads

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(uploads.router, prefix="/upload", tags=["uploads"])

# NEW: Development-only testing endpoints
@app.get("/dev/database/stats")
async def dev_database_stats():
    """View database statistics (development only)"""
    from src.database import SessionLocal, User, Assignment
    db = SessionLocal()
    try:
        user_count = db.query(User).count()
        assignment_count = db.query(Assignment).count()
        return {
            "users": user_count,
            "assignments": assignment_count,
            "message": "Development database statistics"
        }
    finally:
        db.close()

@app.post("/dev/database/reset")
async def dev_database_reset():
    """DANGER: Reset development database (dev only)"""
    from src.database import Base, engine
    print("⚠️  Resetting development database...")
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    print("✓ Database reset complete")
    return {"message": "Development database reset successfully"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main_dev:app",
        host="127.0.0.1",
        port=8000,
        reload=True
    )
```

---

## Step 6: Initialize Development Database

Run this script to create all tables in your local database:

Create `api/init_dev_db.py`:

```python
"""
Initialize development database with tables
Run this once to set up local testing environment
"""

import os
from dotenv import load_dotenv
from src.database import engine, Base, SessionLocal, User
from src.routes.auth import hash_password
from datetime import datetime

# Load development environment
load_dotenv('.env.development')

def init_dev_db():
    """Initialize development database"""
    print("\n" + "="*60)
    print("🔧 Initializing Development Database")
    print("="*60)
    
    # Create all tables
    print("📊 Creating tables...")
    Base.metadata.create_all(bind=engine)
    print("✓ Tables created")
    
    # Create default admin user
    print("👤 Creating default admin user...")
    db = SessionLocal()
    
    # Check if admin exists
    admin = db.query(User).filter(User.username == "admin").first()
    
    if not admin:
        admin = User(
            username="admin",
            password_hash=hash_password("NDAY_2026"),
            name="Admin User",
            email="admin@nday.local",
            role="admin",
            is_active=True,
            created_at=datetime.utcnow()
        )
        db.add(admin)
        db.commit()
        print(f"✓ Admin user created (username: admin, password: NDAY_2026)")
    else:
        print("✓ Admin user already exists")
    
    # Create test driver
    print("👤 Creating test driver user...")
    driver = db.query(User).filter(User.username == "testdriver").first()
    
    if not driver:
        driver = User(
            username="testdriver",
            password_hash=hash_password("test123"),
            name="Test Driver",
            email="driver@nday.local",
            role="driver",
            is_active=True,
            created_at=datetime.utcnow()
        )
        db.add(driver)
        db.commit()
        print(f"✓ Test driver created (username: testdriver, password: test123)")
    else:
        print("✓ Test driver already exists")
    
    db.close()
    
    print("\n" + "="*60)
    print("✓ Development database ready!")
    print("="*60)
    print("\n📝 Login Credentials:")
    print("  Admin:  admin / NDAY_2026")
    print("  Driver: testdriver / test123")
    print("\n🚀 Start development server with:")
    print("  cd api")
    print("  python main_dev.py")
    print("\n📍 Access at: http://127.0.0.1:8000")
    print("="*60 + "\n")

if __name__ == "__main__":
    init_dev_db()
```

---

## Step 7: Run Development Server

```bash
cd api
python init_dev_db.py
```

Expected output:
```
============================================================
🔧 Initializing Development Database
============================================================
📊 Creating tables...
✓ Tables created
👤 Creating default admin user...
✓ Admin user created (username: admin, password: NDAY_2026)
👤 Creating test driver user...
✓ Test driver created (username: testdriver, password: test123)

============================================================
✓ Development database ready!
============================================================
```

Now start the server:

```bash
python main_dev.py
```

Expected output:
```
============================================================
🚀 NDAY Route Manager - DEVELOPMENT MODE
============================================================
Environment: development
Database: postgresql://postgres:postgres@localhost:5432/nday_om_dev
API: http://127.0.0.1:8000
============================================================
⚙️  Initializing database...
✓ Database tables ready

INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Press CTRL+C to quit
```

---

## Step 8: Test Locally

### Test 1: Health Check
```bash
curl http://127.0.0.1:8000/health
```

### Test 2: Login
```bash
curl -X POST http://127.0.0.1:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"NDAY_2026"}'
```

### Test 3: Database Stats
```bash
curl http://127.0.0.1:8000/dev/database/stats
```

### Test 4: Reset Database (if needed)
```bash
curl -X POST http://127.0.0.1:8000/dev/database/reset
```

---

## Step 9: Use Postman for API Testing

1. Download Postman: https://www.postman.com/downloads/
2. Create a new collection "NDAY Development"
3. Add requests:
   - `POST http://127.0.0.1:8000/auth/login`
   - `GET http://127.0.0.1:8000/dev/database/stats`
   - Test all endpoints

---

## Switching Between Production & Development

### Keep Production Live (Unchanged):
```bash
# This is what's running on Render - DON'T TOUCH
# Current: nday-om.onrender.com
# Still uses: users.json + in-memory storage
```

### Development (Local Only):
```bash
cd api
python main_dev.py

# Runs on: http://127.0.0.1:8000
# Uses: PostgreSQL database (nday_om_dev)
# Completely isolated
```

---

## File Structure

```
NDAY_OM/
├── api/
│   ├── main.py                 ← Current production (unchanged)
│   ├── main_dev.py             ← NEW: Development version
│   ├── init_dev_db.py          ← NEW: Initialize dev database
│   ├── src/
│   │   ├── database.py         ← NEW: SQLAlchemy models
│   │   ├── routes/
│   │   │   ├── auth.py
│   │   │   └── uploads.py
│   │   └── ...
│   ├── requirements.txt        ← Updated with SQLAlchemy
│   ├── users.json              ← Production data (unchanged)
│   ├── .env                    ← Production config (unchanged)
│   ├── .env.development        ← NEW: Development config
│   └── .gitignore              ← Add .env.development
└── ...
```

---

## Next Steps

1. ✅ Install PostgreSQL locally
2. ✅ Create `nday_om_dev` database
3. ✅ Create `api/.env.development`
4. ✅ Create `api/main_dev.py` and `api/init_dev_db.py`
5. ✅ Run `python api/init_dev_db.py`
6. ✅ Run `python api/main_dev.py`
7. ✅ Test endpoints with curl or Postman
8. ✅ Develop new features (assignments, metrics, incidents, etc.)
9. ✅ Build reports and dashboards
10. ✅ **Only push to Render when fully tested**

---

## Safety Guarantees

✅ Your live website is completely unchanged  
✅ Development database is local only  
✅ No risk of accidental data loss  
✅ Can test at your own pace  
✅ Easy to reset development DB with `/dev/database/reset`  
✅ Production data is safe in `users.json`  

---

**Status:** Ready to implement  
**Last Updated:** February 23, 2026
