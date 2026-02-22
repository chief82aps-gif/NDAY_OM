# NDAY Route Manager - Upgrade & Feature Backlog

## Overview
This document tracks potential improvements, features, and integrations for the NDAY Route Manager system. Items are organized by category and priority.

**Canonical source:** This file is the single source of truth for feature ideas and upgrades. Legacy notes from `IDEAS.md` were merged here on Feb 22, 2026.

### 📨 Idea Intake Queue
- Asana integration for new driver scheduling (merged into High Priority item #1)
- Performance metrics by driver and route code (covered in Medium Priority item #8)
- Motivational phrase generator using driver metrics (tracked in Medium Priority item #8.1)

---

## ✅ Completed Features
- Route Sheet PDF parsing (multi-page support, A/B/E/G zone support)
- Driver assignment and vehicle allocation
- Driver handout PDF generation
- Driver schedule ingest (Excel with 2 tabs)
- Show time calculation with wave consolidation
- Sweeper identification
- Driver schedule PDF report generation
- Daily driver assignment dashboard
- Assignment database with search/filter
- Admin panel for user management

---

## 🚀 High Priority Features

### 1. Asana Integration (Hiring/Onboarding)
**Idea:** Integrate with Asana to pull new hire candidates and auto-schedule them
- Pull onboarding status from Asana
- Create scheduling suggestions based on team load balancing
- Auto-assign to waves/days to level-load the team
- Push scheduling back to Asana as task assignments
- **Dependencies:** Asana API key, project/section mapping

### 2. Multi-Week Schedule Planning
**Idea:** Extend schedule to support recurring schedules across multiple weeks
- Upload template schedules
- Generated schedules for multiple weeks ahead
- Identify scheduling conflicts/gaps
- Show year-long driver availability
- Alert on low coverage days

### 3. Real-Time Driver Status Updates
**Idea:** Track driver status throughout the day (arriving, on route, returning, completed)
- Integration with delivery tracking system
- Update assignment database with actual completion times
- Generate performance reports (on-time, efficiency, etc.)
- Alert system for delays or issues

### 4. Driver Preferences & Constraints
**Idea:** Allow drivers to input availability/preferences directly
- Self-service portal for drivers to set availability
- Preferred shift/wave times
- Geographic preferences (zones they like)
- Days off and recurring time off
- Integration with schedule generation algorithm

---

## 🎯 Medium Priority Features

### 5. Advanced Load Balancing Algorithm
**Idea:** Improve scheduling to automatically optimize across multiple metrics
- Equal distribution of packages/weight per driver
- Equal distribution of stops/deliveries
- Balanced experience levels (mix of experienced + new drivers)
- Route difficulty rating
- Driver skill affinity (preferred routes/vehicles)
- **Current:** Basic wave consolidation; upgrade to multi-factor optimization

### 6. Sweeper Management Workflow
**Idea:** Streamline sweeper dispatch and tracking
- Sweeper queue display (who's available for dispatch)
- Sweeper assignment history
- Performance metrics (avg response time, completion rate)
- Notification system when sweepers are needed
- Geographic routing for sweepers

### 7. Vehicle Rotation & Maintenance Alerts
**Idea:** Track vehicle usage and maintenance schedules
- Log vehicle mileage per route
- Alert when vehicle maintenance due
- Track vehicle issues/defects
- Rotation schedule to prevent overuse of specific vans
- Integration with fleet management system

### 8. Performance Analytics Dashboard
**Idea:** Deep insights into team performance
- Driver KPIs (on-time %, average delivery time, customer ratings)
- Route efficiency metrics
- Vehicle utilization rates
- Peak hours/demand analysis
- Trend analysis over time
- Predictive analytics for scheduling

### 8.1 Motivational Phrase Generator
**Idea:** Use driver performance and safety metrics to select the most effective footer message per driver
- Track phrase impact on key metrics (safety incidents, on-time return, delivery accuracy)
- A/B test motivational and safety comments by route/driver cohort
- Recommend best-performing phrase for each driver profile
- Keep manual override for dispatcher/admins

---

## 💡 Medium-Low Priority Features

### 9. SMS/Email Notifications
**Idea:** Automated communications to drivers and managers
- Driver assignment notifications (day before, morning of)
- Show time reminders
- Route changes/updates
- Sweeper dispatch alerts
- Manager alerts for staffing issues
- Customizable notification preferences

### 10. Driver Schedule Self-Service Portal
**Idea:** Allow drivers to view/manage their own schedule
- View assigned routes and show times
- Switch shifts with other drivers (with approval)
- Request time off
- View historical assignments
- Availability calendar management

### 11. Route Batch Processing
**Idea:** Process multiple route sheets at once
- Bulk upload multiple PDFs
- Parallel processing
- Consolidated report generation
- Duplicate detection and handling

### 12. Template/Recurring Schedules
**Idea:** Create reusable schedule templates
- Save schedule templates
- Apply templates for recurring weeks
- Quick scheduling for "standard" weeks
- Variation templates (holiday weeks, high-volume, etc.)

---

## 🔧 Technical Improvements

### 13. Backend Optimization
**Ideas:**
- Add request pagination for large datasets
- Implement caching for frequently accessed data
- Optimize PDF generation (current: ReportLab, consider: better streaming)
- Add database layer (currently in-memory; consider: SQLite or PostgreSQL)
- Rate limiting and API security enhancements

### 14. Frontend Enhancements
**Ideas:**
- Real-time updates using WebSockets
- Mobile-responsive design improvements
- Dark mode support
- Keyboard shortcuts for power users
- Drag-and-drop for manual schedule adjustments
- Export to iCal for driver calendar integration

### 15. Data Export & Reporting
**Ideas:**
- Export schedules to CSV/Excel
- Export to Google Calendar
- Generate custom reports
- Email report delivery (scheduled)
- Historical data archival

### 16. System Reliability
**Ideas:**
- Add comprehensive error logging
- Implement retry logic for file uploads
- Add data backup/recovery system
- Health check monitoring
- Graceful degradation on partial failures

---

## 🔌 Integration Opportunities

### 17. Asana (Discussed Above)
- Hiring/onboarding pipeline integration
- Task assignment and tracking
- Status reporting

### 18. Google Calendar / Outlook
- Export driver schedules to personal calendars
- Show driver availability in real-time
- Sync with corporate calendar

### 19. Slack Integration
- Daily schedule summaries
- Alerts and notifications
- Quick commands (e.g., `/sweeper-status`)
- Report delivery to channels

### 20. Twilio SMS
- Send schedule notifications via text
- Driver confirmations
- Route updates

### 21. Custom Vehicle Telematics
- Real-time GPS tracking
- ETA calculations
- Route deviation detection
- Fuel/charging management

### 22. Google Maps API
- Route optimization
- Drive time calculations
- Traffic-aware scheduling
- Zone mapping visualization

---

## 📊 Reporting & Insights

### 23. Dashboard Enhancements
**Current:** Basic upload/assignment/database views
**Proposed:**
- Live daily dashboard (current assignments, in-progress, completed)
- Team roster with utilization percentages
- KPI cards (on-time %, packages delivered, efficiency)
- Geographic heat map of current deliveries

### 24. Historical Analytics
- Driver performance over time
- Route performance trends
- Seasonal demand patterns
- Staffing optimization recommendations

### 25. Forecast & Planning
- Demand forecasting for upcoming weeks
- Staffing requirements prediction
- Hiring recommendations
- Capacity planning

---

## 🎓 Training & Onboarding

### 26. Interactive Tutorials
- In-app guided tours for new users
- Video demos for key features
- Help documentation with screenshots

### 27. Driver Onboarding Workflow
- Automated welcome emails
- Required training modules
- System orientation
- Quick-start guide

---

## 🔐 Security & Compliance

### 28. Enhanced Access Control
- Role-based permissions (admin, manager, driver, sweeper)
- Audit logging for data changes
- Two-factor authentication
- Session management

### 29. Data Privacy
- GDPR/CCPA compliance
- Data retention policies
- Personal data masking
- Encryption for sensitive fields

---

## 🐛 Known Issues to Address
- [ ] Ensure upload directory resilience in Render environment
- [ ] Handle large file uploads more gracefully
- [ ] Improve error messaging for common failures
- [ ] Add retry logic for network failures
- [ ] Handle timezone differences in display

---

## 📝 Notes for Implementation

### Quick Wins (Could do this week)
- SMS notifications using Twilio
- Export to CSV
- Slack integration
- Driver portal (view-only initially)

### Medium Effort (1-2 weeks)
- Asana integration basics
- Advanced load balancing
- Vehicle maintenance tracking
- Performance dashboard

### Larger Projects (3+ weeks)
- Real-time tracking system
- Comprehensive analytics
- Database migration
- Mobile app

---

## 💬 Discussion Items
1. **Priority:** What matters most right now?
2. **Asana:** Should this be priority #1 for hiring workflow?
3. **Database:** Should we migrate to persistent DB sooner?
4. **Mobile:** Is mobile app needed or just mobile-responsive web?
5. **Integrations:** Which external systems are most critical?

---

Last Updated: February 22, 2026
