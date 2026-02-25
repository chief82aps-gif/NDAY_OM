# NDAY Route Manager - Database Schema

**Status:** Implementation Plan  
**Database:** PostgreSQL  
**ORM:** SQLAlchemy 2.0  
**Date Created:** February 23, 2026

---

## Overview

This document defines the complete database schema for the NDAY Route Manager system. It replaces the current in-memory storage with persistent PostgreSQL.

---

## Connection String

```
postgresql://username:password@host:port/nday_om
```

**For Render Environment:**
```python
DATABASE_URL = os.getenv('DATABASE_URL')  # Render auto-provides this
```

---

## Core Tables

### 1. **users** - System users and admins
```sql
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    name VARCHAR(100),
    email VARCHAR(100),
    role VARCHAR(20),  -- 'admin', 'manager', 'driver', 'dispatcher'
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP
);
```

### 2. **drivers** - Driver profiles
```sql
CREATE TABLE drivers (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    employee_id VARCHAR(50) UNIQUE,
    phone VARCHAR(20),
    hire_date DATE,
    status VARCHAR(20),  -- 'active', 'inactive', 'on_leave'
    experience_level VARCHAR(20),  -- 'new', 'intermediate', 'experienced'
    preferred_zones TEXT,  -- JSON: ["A", "B", "E"]
    license_expiry DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 3. **vehicles** - Fleet vehicles (vans)
```sql
CREATE TABLE vehicles (
    id SERIAL PRIMARY KEY,
    vin VARCHAR(50) UNIQUE NOT NULL,
    vehicle_name VARCHAR(100),  -- e.g., "1901 XL"
    service_type VARCHAR(50),  -- 'Standard Parcel', 'Oversized', 'Electric'
    capacity_cubic_feet DECIMAL(10,2),
    capacity_weight_lbs DECIMAL(10,2),
    status VARCHAR(20),  -- 'active', 'grounded', 'maintenance'
    is_electric BOOLEAN DEFAULT FALSE,
    acquisition_date DATE,
    mileage_current INTEGER,
    last_maintenance_date DATE,
    next_maintenance_due DATE,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 4. **assignments** - Route assignments
```sql
CREATE TABLE assignments (
    id SERIAL PRIMARY KEY,
    assignment_id VARCHAR(50) UNIQUE NOT NULL,  -- e.g., "CX139"
    route_code VARCHAR(50) NOT NULL,
    driver_id INTEGER REFERENCES drivers(id),
    vehicle_id INTEGER REFERENCES vehicles(id),
    service_type VARCHAR(50),
    wave_time TIME,
    scheduled_show_time TIME,
    actual_show_time TIME,
    scheduled_return_time TIME,
    actual_return_time TIME,
    zone VARCHAR(10),
    is_sweeper BOOLEAN DEFAULT FALSE,
    assignment_date DATE NOT NULL,
    status VARCHAR(20),  -- 'pending', 'assigned', 'in_progress', 'completed', 'tbd'
    tbd_reason TEXT,  -- Why it's TBD if status='tbd'
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_driver_date (driver_id, assignment_date),
    INDEX idx_vehicle_date (vehicle_id, assignment_date),
    INDEX idx_assignment_date (assignment_date)
);
```

### 5. **performance_metrics** - Driver KPIs
```sql
CREATE TABLE performance_metrics (
    id SERIAL PRIMARY KEY,
    driver_id INTEGER REFERENCES drivers(id) ON DELETE CASCADE,
    metric_date DATE NOT NULL,
    assignments_scheduled INTEGER DEFAULT 0,
    assignments_completed INTEGER DEFAULT 0,
    on_time_count INTEGER DEFAULT 0,
    late_count INTEGER DEFAULT 0,
    on_time_percentage DECIMAL(5,2),
    total_packages INTEGER DEFAULT 0,
    total_weight_lbs DECIMAL(12,2),
    customer_rating_avg DECIMAL(3,2),
    rescues_performed INTEGER DEFAULT 0,
    safety_incidents INTEGER DEFAULT 0,
    communication_score VARCHAR(20),  -- 'excellent', 'good', 'fair', 'poor'
    efficiency_score VARCHAR(20),
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_driver_date (driver_id, metric_date)
);
```

### 6. **incidents** - Accident and damage reports
```sql
CREATE TABLE incidents (
    id SERIAL PRIMARY KEY,
    incident_id VARCHAR(50) UNIQUE NOT NULL,
    driver_id INTEGER REFERENCES drivers(id),
    vehicle_id INTEGER REFERENCES vehicles(id),
    assignment_id INTEGER REFERENCES assignments(id),
    incident_type VARCHAR(50),  -- 'accident', 'damage', 'safety', 'customer_complaint'
    severity_level VARCHAR(20),  -- 'low', 'medium', 'high', 'critical'
    incident_date TIMESTAMP NOT NULL,
    location VARCHAR(255),
    latitude DECIMAL(10,8),
    longitude DECIMAL(11,8),
    description TEXT,
    driver_statement TEXT,
    is_reported_to_insurance BOOLEAN DEFAULT FALSE,
    requires_follow_up BOOLEAN DEFAULT FALSE,
    status VARCHAR(20),  -- 'reported', 'under_review', 'resolved'
    resolution_notes TEXT,
    photos_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,
    INDEX idx_driver_date (driver_id, incident_date),
    INDEX idx_vehicle_date (vehicle_id, incident_date)
);
```

### 7. **incident_photos** - Photo evidence for incidents
```sql
CREATE TABLE incident_photos (
    id SERIAL PRIMARY KEY,
    incident_id INTEGER REFERENCES incidents(id) ON DELETE CASCADE,
    photo_url VARCHAR(500),  -- S3 URL
    photo_key VARCHAR(255),  -- S3 object key
    caption TEXT,
    upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 8. **rescues** - Rescue tracking for bonuses
```sql
CREATE TABLE rescues (
    id SERIAL PRIMARY KEY,
    rescue_id VARCHAR(50) UNIQUE NOT NULL,
    driver_id INTEGER REFERENCES drivers(id),
    rescue_date DATE NOT NULL,
    rescue_type VARCHAR(50),  -- 'traffic_control', 'assistance', 'safety_threat', etc.
    location VARCHAR(255),
    description TEXT,
    bonus_amount DECIMAL(10,2),
    bonus_status VARCHAR(20),  -- 'pending', 'approved', 'paid'
    approval_date DATE,
    approved_by INTEGER REFERENCES users(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_driver_date (driver_id, rescue_date)
);
```

### 9. **van_inspections** - Vehicle condition checks
```sql
CREATE TABLE van_inspections (
    id SERIAL PRIMARY KEY,
    vehicle_id INTEGER REFERENCES vehicles(id),
    inspection_date TIMESTAMP NOT NULL,
    inspector_user_id INTEGER REFERENCES users(id),
    inspection_type VARCHAR(20),  -- 'pre_shift', 'post_shift', 'maintenance'
    overall_condition VARCHAR(20),  -- 'excellent', 'good', 'fair', 'poor'
    fuel_level INTEGER,  -- 0-100 percentage
    cleanliness_score INTEGER,  -- 1-10
    damage_areas TEXT,  -- JSON array of damage descriptions
    notes TEXT,
    photos_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_vehicle_date (vehicle_id, inspection_date)
);
```

### 10. **route_sheets** - Uploaded route sheets
```sql
CREATE TABLE route_sheets (
    id SERIAL PRIMARY KEY,
    upload_date DATE NOT NULL,
    file_name VARCHAR(255),
    file_size INTEGER,
    s3_location VARCHAR(500),
    total_routes INTEGER,
    total_assignments INTEGER,
    processing_status VARCHAR(20),  -- 'pending', 'processed', 'error'
    error_message TEXT,
    uploaded_by_user_id INTEGER REFERENCES users(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP,
    INDEX idx_upload_date (upload_date)
);
```

---

## Reporting Views

### Daily Summary View
```sql
CREATE VIEW v_daily_summary AS
SELECT
    a.assignment_date,
    COUNT(DISTINCT a.driver_id) as total_drivers,
    COUNT(DISTINCT a.id) as total_assignments,
    SUM(CASE WHEN a.status = 'completed' THEN 1 ELSE 0 END) as completed,
    SUM(CASE WHEN a.actual_show_time <= a.scheduled_show_time THEN 1 ELSE 0 END) as on_time,
    COUNT(DISTINCT a.vehicle_id) as vehicles_used
FROM assignments a
GROUP BY a.assignment_date;
```

### Driver Performance View
```sql
CREATE VIEW v_driver_performance AS
SELECT
    d.id,
    d.employee_id,
    u.name as driver_name,
    pm.metric_date,
    pm.on_time_percentage,
    pm.assignments_completed,
    pm.customer_rating_avg,
    pm.rescues_performed,
    pm.safety_incidents,
    pm.efficiency_score
FROM drivers d
JOIN users u ON d.user_id = u.id
LEFT JOIN performance_metrics pm ON d.id = pm.driver_id
ORDER BY d.id, pm.metric_date DESC;
```

### Incident Summary View
```sql
CREATE VIEW v_incident_summary AS
SELECT
    DATE(i.incident_date) as incident_date,
    i.incident_type,
    i.severity_level,
    COUNT(*) as count,
    COUNT(DISTINCT i.driver_id) as unique_drivers,
    COUNT(DISTINCT i.vehicle_id) as unique_vehicles
FROM incidents i
WHERE i.incident_date >= CURRENT_DATE - INTERVAL '90 days'
GROUP BY DATE(i.incident_date), i.incident_type, i.severity_level
ORDER BY DATE(i.incident_date) DESC;
```

---

## Indexes for Performance

```sql
CREATE INDEX idx_users_active ON users(is_active);
CREATE INDEX idx_drivers_status ON drivers(status);
CREATE INDEX idx_vehicles_status ON vehicles(status);
CREATE INDEX idx_assignments_status ON assignments(status);
CREATE INDEX idx_incidents_severity ON incidents(severity_level);
CREATE INDEX idx_rescues_status ON rescues(bonus_status);

-- Composite indexes for common queries
CREATE INDEX idx_assignment_lookup ON assignments(driver_id, assignment_date, status);
CREATE INDEX idx_metrics_lookup ON performance_metrics(driver_id, metric_date DESC);
CREATE INDEX idx_incident_lookup ON incidents(driver_id, incident_date DESC);
```

---

## Migration Strategy

### Phase 1: Schema Creation
- Create all tables
- Create indexes
- Create views

### Phase 2: Data Migration
- Import existing users from JSON
- Create default admin account
- Preserve assignment history if available

### Phase 3: API Integration
- Update endpoints to query PostgreSQL
- Implement database transactions
- Add error handling
- Deploy to Render

### Phase 4: Verification
- Test all endpoints
- Verify data integrity
- Monitor performance
- Rollback plan if needed

---

## Connection Pool Configuration

For Render environment with limited connections:

```python
from sqlalchemy.pool import QueuePool

engine = create_engine(
    DATABASE_URL,
    poolclass=QueuePool,
    pool_size=5,
    max_overflow=10,
    pool_recycle=3600,
    pool_pre_ping=True  # Verify connections before use
)
```

---

## Backup & Recovery

- **Daily automated backups** via Render
- **Point-in-time recovery** available
- **Export to CSV** for periodic snapshots
- **Archive old data** (>1 year) to S3

---

## Performance Expectations

| Operation | Time |
|-----------|------|
| Get daily assignments | < 100ms |
| Get driver metrics | < 200ms |
| Submit incident report | < 500ms |
| Generate daily report | < 2 seconds |
| Generate monthly analytics | < 5 seconds |

---

## Related Documents
- [MOBILE_APP_REQUIREMENTS.md](MOBILE_APP_REQUIREMENTS.md)
- [VAN_INGEST_RULES.md](VAN_INGEST_RULES.md)

---

**Last Updated:** February 23, 2026  
**Next Review:** March 1, 2026
