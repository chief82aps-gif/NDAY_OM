"""
Initialize development database with tables and sample data
Run this once before starting main_dev.py for the first time
"""

import os
import sys
from pathlib import Path

# Add api directory to path
api_dir = Path(__file__).parent
sys.path.insert(0, str(api_dir))

# Load environment variables FIRST before importing database
def load_env_development():
    """Load development environment variables"""
    env_file = api_dir / '.env.development'
    if env_file.exists():
        print("✓ Loading .env.development")
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key] = value
    else:
        # Set default values
        print("✓ Using default DATABASE_URL (localhost:5433)")
        os.environ['DATABASE_URL'] = 'postgresql://postgres:Lapmonkey26@localhost:5433/nday_om_dev'
        os.environ['ENVIRONMENT'] = 'development'

# Load environment BEFORE importing database module
load_env_development()

from src.database import engine, Base, SessionLocal, User, Driver
from datetime import datetime
import hashlib

def hash_password(password: str) -> str:
    """Simple password hashing for development"""
    return hashlib.sha256(password.encode()).hexdigest()


def init_dev_db():
    """Initialize development database"""
    print("\n" + "="*60)
    print("🔧 Initializing Development Database")
    print("="*60)
    
    print(f"📍 Database: {os.getenv('DATABASE_URL', 'Not set')}")
    
    # Create all tables
    print("\n📊 Creating tables...")
    try:
        Base.metadata.create_all(bind=engine)
        print("✓ Tables created successfully")
    except Exception as e:
        print(f"✗ Error creating tables: {e}")
        return False
    
    # Create session
    db = SessionLocal()
    
    try:
        # Create default admin user
        print("\n👤 Creating default admin user...")
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
            print("✓ Admin user created")
            print("  Username: admin")
            print("  Password: NDAY_2026")
        else:
            print("✓ Admin user already exists")
        
        # Create test driver user
        print("\n👤 Creating test driver user...")
        driver_user = db.query(User).filter(User.username == "testdriver").first()
        
        if not driver_user:
            driver_user = User(
                username="testdriver",
                password_hash=hash_password("test123"),
                name="Test Driver",
                email="driver@nday.local",
                role="driver",
                is_active=True,
                created_at=datetime.utcnow()
            )
            db.add(driver_user)
            db.commit()
            
            # Create driver profile
            driver_profile = Driver(
                user_id=driver_user.id,
                employee_id="DRV001",
                phone="555-0100",
                status="active",
                experience_level="intermediate"
            )
            db.add(driver_profile)
            db.commit()
            
            print("✓ Test driver created")
            print("  Username: testdriver")
            print("  Password: test123")
        else:
            print("✓ Test driver already exists")
        
        # Verify table creation
        print("\n📊 Verifying database...")
        user_count = db.query(User).count()
        print(f"✓ Users in database: {user_count}")
        
        print("\n" + "="*60)
        print("✅ Development database initialized successfully!")
        print("="*60)
        print("\n📝 Login Credentials:")
        print("  Admin:  admin / NDAY_2026")
        print("  Driver: testdriver / test123")
        print("\n🚀 Start development server:")
        print("  cd api")
        print("  python main_dev.py")
        print("\n📍 Access at: http://127.0.0.1:8000")
        print("📍 API Docs: http://127.0.0.1:8000/docs")
        print("="*60 + "\n")
        
        return True
        
    except Exception as e:
        db.rollback()
        print(f"\n✗ Error: {e}")
        print("\nTroubleshooting:")
        print("1. Verify PostgreSQL is running")
        print("2. Check DATABASE_URL in .env.development")
        print("3. Ensure database 'nday_om_dev' exists")
        return False
        
    finally:
        db.close()


if __name__ == "__main__":
    print("\n⚙️  NDAY Route Manager - Database Initialization")
    success = init_dev_db()
    sys.exit(0 if success else 1)
