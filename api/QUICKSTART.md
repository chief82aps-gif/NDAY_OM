# Quick Start Guide - PostgreSQL Development

## ✅ Installation Complete!

All development files are created and packages installed.

---

## 📋 What Was Created

1. ✅ `api/main_dev.py` - Development server (copy of main.py + PostgreSQL)
2. ✅ `api/init_dev_db.py` - Database initializer
3. ✅ `api/.env.development` - Development environment config
4. ✅ `api/src/database.py` - SQLAlchemy models
5. ✅ Updated `api/requirements.txt` - Added database packages
6. ✅ Updated `.gitignore` - Protected your local dev environment

---

## 🚀 Next Steps

### Step 1: Install PostgreSQL Locally

**Option A: Download Installer (Recommended for Windows)**
1. Go to: https://www.postgresql.org/download/windows/
2. Download and run installer
3. During setup:
   - Username: `postgres`
   - Password: `postgres` (or choose your own)
   - Port: `5432`
4. Remember your password!

**Option B: Use Existing PostgreSQL Account**
If you already have PostgreSQL running locally, skip to Step 2.

---

### Step 2: Create Local Database

Open PostgreSQL command line:

**Windows** (search for "psql" or "SQL Shell"):
```sql
-- Login as postgres user (enter your password when prompted)
-- Then run:
CREATE DATABASE nday_om_dev;
\l  -- List databases to verify
\q  -- Exit
```

**Or use pgAdmin** (graphical tool installed with PostgreSQL):
1. Open pgAdmin
2. Right-click "Databases"
3. Create → Database
4. Name: `nday_om_dev`
5. Click Save

---

### Step 3: Update Connection String (if needed)

If you used a different password or settings, edit `api/.env.development`:

```
DATABASE_URL=postgresql://YOUR_USERNAME:YOUR_PASSWORD@localhost:5432/nday_om_dev
```

Default is:
```
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/nday_om_dev
```

---

### Step 4: Initialize Database

Open terminal in VS Code:

```bash
cd C:\Users\chief\NDAY_OM\api
C:/Users/chief/NDAY_OM/.venv/Scripts/python.exe init_dev_db.py
```

Expected output:
```
============================================================
🔧 Initializing Development Database
============================================================
📍 Database: postgresql://postgres:postgres@localhost:5432/nday_om_dev
📊 Creating tables...
✓ Tables created successfully
👤 Creating default admin user...
✓ Admin user created
  Username: admin
  Password: NDAY_2026
👤 Creating test driver user...
✓ Test driver created
  Username: testdriver
  Password: test123
============================================================
✅ Development database initialized successfully!
============================================================
```

---

### Step 5: Start Development Server

```bash
C:/Users/chief/NDAY_OM/.venv/Scripts/python.exe main_dev.py
```

Expected output:
```
============================================================
🚀 NDAY Route Manager - DEVELOPMENT MODE
============================================================
Database: PostgreSQL (local)
API: http://127.0.0.1:8000
============================================================
⚙️  Initializing database...
✓ Database tables ready

INFO:     Uvicorn running on http://127.0.0.1:8000
```

---

### Step 6: Test It

**Open browser:** http://127.0.0.1:8000

You should see:
```json
{
  "message": "NDAY_OM API is running (DEVELOPMENT MODE)",
  "database": "PostgreSQL (local)",
  "environment": "development"
}
```

**Test health check:** http://127.0.0.1:8000/dev/health

---

## 🔧 Troubleshooting

### "Connection refused" or "could not connect to server"
- ✅ Verify PostgreSQL is running (check Windows Services or pgAdmin)
- ✅ Check port 5432 is not blocked
- ✅ Verify database `nday_om_dev` exists

### "Authentication failed"
- ✅ Check password in `.env.development`
- ✅ Try connecting with pgAdmin to verify credentials

### "Module 'psycopg2' not found"
- ✅ Run: `C:/Users/chief/NDAY_OM/.venv/Scripts/python.exe -m pip install psycopg2-binary`

### Database already exists
- ✅ Safe to run `init_dev_db.py` again - it checks for existing data

---

## 📝 Development Workflow

1. **Start dev server**: `python main_dev.py`
2. **Test endpoints**: Use browser, Postman, or curl
3. **Reset database** (if needed): POST to http://127.0.0.1:8000/dev/database/reset
4. **Stop server**: Press Ctrl+C

---

## 🛡️ Safety Notes

✅ Your **production website** (Render) is completely unchanged
✅ `main.py` still uses `users.json`
✅ Development database is local only
✅ `.env.development` is in `.gitignore` (won't commit)
✅ Can't accidentally deploy dev code (Render uses `main.py`)

---

## 📚 What's Next

Once your development environment is working:

1. Test existing endpoints with PostgreSQL
2. View real database data
3. Build new reporting features
4. Test assignment tracking
5. Develop performance metrics
6. **Only** push to production when fully tested

---

**Status:** Ready to test!  
**Need help?** Check [LOCAL_DEVELOPMENT_SETUP.md](../Governance/LOCAL_DEVELOPMENT_SETUP.md)
