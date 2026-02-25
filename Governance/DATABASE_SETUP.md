# PostgreSQL Database Setup Guide

**Date:** February 23, 2026  
**Status:** Ready for Implementation

---

## overview

This guide walks you through setting up PostgreSQL for the NDAY Route Manager. Since you already have a PostgreSQL account, we'll connect your existing instance.

---

## Prerequisites

✅ PostgreSQL account created  
✅ Database server running  
✅ Connection details available (host, username, password, database name)  

---

## Step 1: Install Python Packages

Add to your `api/requirements.txt`:

```
sqlalchemy==2.0.23
psycopg2-binary==2.9.9
```

Then install:

```bash
cd api
pip install -r requirements.txt
```

---

## Step 2: Set Environment Variables

### Local Development (.env file)
Create or update `api/.env`:

```
DATABASE_URL=postgresql://your_username:your_password@your_host:5432/nday_om
```

### Render Deployment
Render automatically detects PostgreSQL service. Add the DATABASE_URL environment variable in Render dashboard:
1. Go to your Render service
2. Environment → Add Variable
3. Key: `DATABASE_URL`
4. Value: `postgresql://username:password@host:5432/nday_om`

---

## Step 3: Initialize the Database

### Create tables using SQLAlchemy

Create `api/init_db.py`:

```python
from src.database import init_db

if __name__ == "__main__":
    init_db()
    print("✓ All tables created successfully!")
```

Run it:

```bash
python api/init_db.py
```

Expected output:
```
✓ Database initialized
```

---

## Step 4: Verify Connection

Test your connection:

```bash
python
```

```python
from src.database import SessionLocal, User
db = SessionLocal()
result = db.query(User).count()
print(f"✓ Connected! Users in database: {result}")
db.close()
```

---

## Step 5: Update FastAPI Main

Update `api/main.py` to use database:

```python
from src.database import init_db, get_db
from sqlalchemy.orm import Session

# Initialize database on startup
@app.on_event("startup")
async def startup():
    init_db()
    print("✓ Database ready")

# Update your routes to use: db: Session = Depends(get_db)
```

---

## Step 6: Migrate Existing Data

### Import users from users.json

Create `api/migrate_users.py`:

```python
import json
from datetime import datetime
from src.database import SessionLocal, User, init_db
from src.routes.auth import hash_password

def migrate_users():
    """Import users from users.json to PostgreSQL"""
    init_db()
    db = SessionLocal()
    
    try:
        # Load existing users
        with open('users.json', 'r') as f:
            users_data = json.load(f)
        
        for user_info in users_data:
            # Check if already exists
            existing = db.query(User).filter(
                User.username == user_info['username']
            ).first()
            
            if not existing:
                new_user = User(
                    username=user_info['username'],
                    password_hash=user_info['password_hash'],
                    name=user_info.get('name', user_info['username']),
                    role=user_info.get('role', 'driver'),
                    is_active=True,
                    created_at=datetime.utcnow()
                )
                db.add(new_user)
        
        db.commit()
        print("✓ Users migrated successfully")
    
    except Exception as e:
        db.rollback()
        print(f"✗ Migration failed: {e}")
    
    finally:
        db.close()

if __name__ == "__main__":
    migrate_users()
```

Run it:

```bash
python api/migrate_users.py
```

---

## Step 7: Backup users.json (Keep it!)

Keep your `users.json` as a backup. You can also set it as a fallback:

```python
# In your auth endpoint
try:
    user = db.query(User).filter(User.username == username).first()
except:
    # Fallback to JSON if database unavailable
    with open('users.json') as f:
        users = json.load(f)
    # authenticate from JSON
```

---

## Testing the Setup

### Test 1: Create New User

```python
from src.database import SessionLocal, User
from src.routes.auth import hash_password

db = SessionLocal()
new_user = User(
    username="testdriver",
    password_hash=hash_password("testpass123"),
    name="Test Driver",
    role="driver"
)
db.add(new_user)
db.commit()
print(f"✓ Created user: {new_user.id}")
db.close()
```

### Test 2: Query Users

```python
from src.database import SessionLocal, User

db = SessionLocal()
users = db.query(User).filter(User.is_active == True).all()
print(f"✓ Active users: {len(users)}")
for u in users:
    print(f"  - {u.username} ({u.role})")
db.close()
```

### Test 3: Create Assignment

```python
from src.database import SessionLocal, Assignment, Driver
from datetime import date

db = SessionLocal()
assignment = Assignment(
    assignment_id="TEST123",
    route_code="CX200",
    assignment_date=date.today(),
    status="pending"
)
db.add(assignment)
db.commit()
print(f"✓ Created assignment: {assignment.id}")
db.close()
```

---

## Updating Existing API Endpoints

### Before (In-Memory):
```python
@router.get("/auth/list-users")
async def list_users(admin_password: str):
    with open('users.json') as f:
        users = json.load(f)
    return users
```

### After (PostgreSQL):
```python
from sqlalchemy.orm import Session
from fastapi import Depends
from src.database import get_db, User

@router.get("/auth/list-users")
async def list_users(admin_password: str, db: Session = Depends(get_db)):
    # Verify admin password
    admin = db.query(User).filter(
        User.username == "admin",
        User.role == "admin"
    ).first()
    
    if not admin or not verify_password(admin_password, admin.password_hash):
        raise HTTPException(status_code=403, detail="Invalid admin password")
    
    users = db.query(User).filter(User.is_active == True).all()
    return [{"username": u.username, "name": u.name, "role": u.role} for u in users]
```

---

## Backup & Recovery

### Automated Backups (Render)
Render includes automatic daily backups. No action needed.

### Manual Backup

```bash
# Export all data to SQL file
pg_dump postgresql://user:pass@host:5432/nday_om > backup_$(date +%Y%m%d).sql

# Or export specific table to CSV
psql postgresql://user:pass@host:5432/nday_om -c "\COPY users TO users_backup.csv WITH CSV HEADER"
```

### Restore from Backup

```bash
# Restore from SQL file
psql postgresql://user:pass@host:5432/nday_om < backup_20260223.sql
```

---

## Troubleshooting

### "Connection refused"
- Verify PostgreSQL server is running
- Check host, port, username, password
- Ensure database exists: `CREATE DATABASE nday_om;`

### "Authentication failed"
- Check password in CONNECTION_URL
- Verify user has privileges: `GRANT ALL ON DATABASE nday_om TO your_user;`

### "Table already exists"
- Safe to ignore - it means tables were already created
- Or drop and recreate: `DROP TABLE IF EXISTS cascade;` then re-run

### "Column 'X' does not exist"
- Ensure all models in `database.py` match your schema
- Check for typos in table names and column names

---

## Next Steps

1. ✅ Install SQLAlchemy and psycopg2
2. ✅ Set DATABASE_URL environment variable
3. ✅ Run `python api/init_db.py` to create tables
4. ✅ Test connection with queries
5. ✅ Migrate existing users.json data
6. ✅ Update API endpoints to use database
7. ✅ Test all endpoints
8. ✅ Deploy to Render
9. ✅ Monitor for issues
10. ✅ Build reports

---

## Report Building (Phase 2)

Once data is being saved to PostgreSQL, we can build reports:

- Daily assignment summaries
- Driver performance dashboards
- Incident analytics
- Vehicle utilization
- Financial audits
- Bonus calculations

---

## References
- [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) - Complete schema definition
- [SQLAlchemy Docs](https://docs.sqlalchemy.org/)
- [PostgreSQL Docs](https://www.postgresql.org/docs/)

---

**Status:** Ready to implement  
**Last Updated:** February 23, 2026
