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

### 9. Dynamic Sort Order by Service Type
**Idea:** Allow users to sort/group assignments by service type as primary sort key
- Dynamically re-order schedule and handouts by service type (standard, oversized, etc.)
- Optional secondary sort by wave time, zone, or driver name
- Filter/group view showing only specific service types
- Remember user's preferred sort preference across sessions

---

## � NEW: Driver-Facing Mobile Apps & Operations (Tier 1 Priority)

### 10. Driver Mobile App
**Idea:** Comprehensive mobile application for drivers to manage assignments and communicate
- View van assignments for the day
- Receive SMS/push notifications with handouts
- Real-time route tracking and status updates
- Quick access to current route details, customer info
- In-app performance metrics dashboard
- **Technology:** React Native or Flutter (code sharing with Next.js frontend)
- **Integrations:** Push notifications (Firebase), SMS (Twilio)

### 11. Incident & Accident Reporting App
**Idea:** Quick mobile tool for drivers to report incidents and accidents
- Photo capture with timestamp/location (GPS)
- Incident type selection (accident, damage, safety issue, customer complaint)
- Driver statement/notes
- Immediate notification to management
- Photo storage in cloud (AWS S3 or similar)
- Integration with incident tracking database
- **Features:**
  - Offline capability (queue reports when reconnected)
  - Auto-attach vehicle/driver/route info
  - Evidence photo library per incident

### 12. Property Checkout/Sign-Out Sheet (Mobile)
**Idea:** Track return of company property and vehicle condition at end of shift
- Digital sign-out form replacing paper sheets
- Vehicle condition checklist (damage, fuel level, cleanliness)
- Company property inventory check-off (tablets, scanners, badges, etc.)
- Photo documentation of vehicle condition
- Digital signature capture
- Real-time alerts if property is missing
- **Integrations:** Van inspection data, property database

### 13. Van Inspection Tool
**Idea:** Structured vehicle inspection with photo library for damage tracking
- Pre-shift vehicle walk-around inspection
- Photo tagging of damage (location, severity)
- Inspection history timeline per vehicle
- Damage severity scoring
- Maintenance alert system (auto-trigger for repeated issues)
- Comparison photos over time to track progression
- **Cloud Storage:** Photo library indexed by VIN and date
- **Reporting:** Generate inspection reports for fleet management

---

## 💰 NEW: Financial & Operational Tools (Tier 1-2 Priority)

### 14. Rescue Tracker & Bonus Calculator
**Idea:** Track rescue incidents and auto-calculate driver bonuses
- Log rescue incidents (time, location, type, driver(s) involved)
- Bonus calculation engine (rules-based: rescue type → bonus amount)
- Manual override capability for special cases
- Bonus report generation for payroll
- Bonus history per driver (transparent to drivers in app)
- Suggestions for team awards (top rescuer, etc.)
- **Payroll Integration:** Export bonus data for Stripe/payroll system
- **Analytics:** Rescue frequency trends, driver rescue performance

### 15. Invoice Audit Tool
**Idea:** Reconcile invoiced work vs. actual work completed
- Import invoice data (CSV or API)
- Compare against actual routes/assignments completed
- Identify discrepancies (unbilled work, over-billing errors)
- Flagging system for investigation
- Reconciliation workflow (approve, dispute, adjust)
- Financial reporting Dashboard
- **Data Source Integration:** Route sheets, daily assignments, driver productivity
- **Export:** Audit reports for finance/legal

### 16. Driver Scorecard & Coaching System
**Idea:** Performance tracking with coaching recommendations
- Automated KPI calculation per driver:
  - On-time completion %
  - Safety incidents rate
  - Customer ratings/feedback
  - Rescue participation
  - Attendance/punctuality
- Performance tiers and behavior patterns
- Coaching suggestions based on weakness areas
- Historical performance trends
- 1-on-1 discussion guide for managers
- Celebration alerts for top performers
- **AI/ML:** Pattern recognition for coaching topics, predictive performance

---

## �💡 Medium-Low Priority Features

### 17. SMS/Email Notifications
**Idea:** Automated communications to drivers and managers
- Driver assignment notifications (day before, morning of)
- Show time reminders
- Route changes/updates
- Sweeper dispatch alerts
- Manager alerts for staffing issues
- Customizable notification preferences

### 18. Driver Schedule Self-Service Portal
**Idea:** Allow drivers to view/manage their own schedule
- View assigned routes and show times
- Switch shifts with other drivers (with approval)
- Request time off
- View historical assignments
- Availability calendar management

### 19. Attendance App with HR Forms
**Idea:** Streamlined call-out, time-off, and HR form management
- Driver call-out/absence reporting
- Time-off request workflow (vacation, sick, personal)
- HR form submission (incident reports, suggestions, feedback)
- Approval workflow for managers
- Audit trail of all requests
- Integration with attendance database
- **Features:**
  - Mobile-first design
  - Push notifications for approvals
  - Suggestion box with routing to management

### 20. Route Batch Processing
**Idea:** Process multiple route sheets at once
- Bulk upload multiple PDFs
- Parallel processing
- Consolidated report generation
- Duplicate detection and handling

### 21. Template/Recurring Schedules
**Idea:** Create reusable schedule templates
- Save schedule templates
- Apply templates for recurring weeks
- Quick scheduling for "standard" weeks
- Variation templates (holiday weeks, high-volume, etc.)

---

## 🔧 Technical Improvements

### 22. Backend Optimization
**Ideas:**
- Add request pagination for large datasets
- Implement caching for frequently accessed data
- Optimize PDF generation (current: ReportLab, consider: better streaming)
- Add database layer (currently in-memory; consider: SQLite or PostgreSQL)
- Rate limiting and API security enhancements

### 23. Frontend Enhancements
**Ideas:**
- Real-time updates using WebSockets
- Mobile-responsive design improvements
- Dark mode support
- Keyboard shortcuts for power users
- Drag-and-drop for manual schedule adjustments
- Export to iCal for driver calendar integration

### 24. Data Export & Reporting
**Ideas:**
- Export schedules to CSV/Excel
- Export to Google Calendar
- Generate custom reports
- Email report delivery (scheduled)
- Historical data archival

### 25. System Reliability
**Ideas:**
- Add comprehensive error logging
- Implement retry logic for file uploads
- Add data backup/recovery system
- Health check monitoring
- Graceful degradation on partial failures

---

## 🔌 Integration Opportunities

### 26. Asana (Discussed Above)
- Hiring/onboarding pipeline integration
- Task assignment and tracking
- Status reporting

### 27. Google Calendar / Outlook
- Export driver schedules to personal calendars
- Show driver availability in real-time
- Sync with corporate calendar

### 28. Slack Integration
- Daily schedule summaries
- Alerts and notifications
- Quick commands (e.g., `/sweeper-status`)
- Report delivery to channels

### 29. Twilio SMS
- Send schedule notifications via text
- Driver confirmations
- Route updates

### 30. Custom Vehicle Telematics
- Real-time GPS tracking
- ETA calculations
- Route deviation detection
- Fuel/charging management

### 31. Google Maps API
- Route optimization
- Drive time calculations
- Traffic-aware scheduling
- Zone mapping visualization

---

## 📊 Reporting & Insights

### 32. Dashboard Enhancements
**Current:** Basic upload/assignment/database views
**Proposed:**
- Live daily dashboard (current assignments, in-progress, completed)
- Team roster with utilization percentages
- KPI cards (on-time %, packages delivered, efficiency)
- Geographic heat map of current deliveries

### 33. Historical Analytics
- Driver performance over time
- Route performance trends
- Seasonal demand patterns
- Staffing optimization recommendations

### 34. Forecast & Planning
- Demand forecasting for upcoming weeks
- Staffing requirements prediction
- Hiring recommendations
- Capacity planning

---

## 🎓 Training & Onboarding

### 35. Interactive Tutorials
- In-app guided tours for new users
- Video demos for key features
- Help documentation with screenshots

### 36. Driver Onboarding Workflow
- Automated welcome emails
- Required training modules
- System orientation
- Quick-start guide

---

## 🔐 Security & Compliance

### 37. Enhanced Access Control
- Role-based permissions (admin, manager, driver, sweeper)
- Audit logging for data changes
- Two-factor authentication
- Session management

### 38. Data Privacy
- GDPR/CCPA compliance
- Data retention policies
- Personal data masking
- Encryption for sensitive fields

---

## � Implementation Priority Matrix

### Quick Wins (Could do this week)
- SMS notifications using Twilio (Item #17)
- Export to CSV (Item #24)
- Slack integration (Item #28)
- Driver portal - view-only initially (Item #18)
- Attendance app basics (Item #19)

### High Impact / Medium Effort (2-4 weeks)
- Driver Mobile App MVP (Item #10) - View assignments, push notifications
- Incident Reporting (Item #11) - Photo capture, incident logging
- Rescue Tracker & Bonus Calculator (Item #14)
- Invoice Audit Tool (Item #15)
- Driver Scorecard System (Item #16)
- Asana integration basics (Item #1 - High Priority)
- Advanced load balancing (Item #5)
- Vehicle maintenance tracking (Item #7)

### Larger Projects (4+ weeks)
- Full Driver Mobile App (Item #10 - with offline, real-time tracking)
- Van Inspection Tool (Item #13) - Full photo library, AI damage detection
- Property Checkout System (Item #12) - Integration with all workflows
- Real-time tracking system (Item #3 - High Priority)
- Comprehensive analytics dashboard (Item #8, #32, #33)
- Database migration from in-memory to persistent (Item #14 - Technical)

---

## 🎯 NEW ITEMS SUMMARY (Feb 23, 2026)
Added 9 new driver-facing and operational tools based on business needs:

**Driver-Facing Mobile (Items #10-13):**
- Driver Mobile App - assignments, notifications, tracking
- Incident & Accident Reporting - photo evidence, GPS tagging
- Sign-Out/Property Checkout - condition reports, inventory
- Van Inspection Tool - damage tracking, photo library

**Financial & Operational (Items #14-16):**
- Rescue Tracker & Bonus Calculator - incident tracking, payroll integration
- Invoice Audit Tool - reconciliation vs. actual work
- Driver Scorecard & Coaching - performance metrics, manager coaching tools

**Plus:**
- Attendance App with HR Forms (Item #19) - call-outs, time-off, suggestions

---

## 💬 Discussion Items
1. **Priority:** What should we tackle first post-stable v1.2?
2. **Mobile App:** React Native vs Flutter vs PWA (progressive web app)?
3. **Database:** Ready to migrate to PostgreSQL for these new features?
4. **Cloud Storage:** S3 for incident photos, inspection photos?
5. **Integrations:** Which systems should we connect first (Asana, Twilio, Stripe)?
6. **Timeline:** What's the realistic roadmap - which quarter for each?

---

Last Updated: February 23, 2026
