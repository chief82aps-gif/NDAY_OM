# Mobile App Development Requirements

**Document Purpose:** Comprehensive guide for building NDAY driver mobile applications  
**Date Created:** February 23, 2026  
**Status:** Planning & Architecture Phase

---

## Overview

The NDAY Route Manager mobile app ecosystem will provide drivers with real-time assignment visibility, secure communication, and operational tools. This document outlines the technical, infrastructure, and resource requirements for development, deployment, and maintenance.

---

## Technology Stack Recommendation

### Framework: **React Native** (Recommended)
- **Language:** JavaScript/TypeScript
- **Advantage:** Code sharing with existing Next.js frontend
- **Platforms:** iOS + Android from single codebase
- **Learning Curve:** Minimal for existing React developers
- **Ecosystem:** Mature, large community, extensive libraries

**Alternatives Considered:**
- **Flutter:** Better performance, separate codebase, Dart learning curve
- **PWA:** Web-based, no app stores, limited offline capability

### Build Tools
- **Scaffolding:** Expo CLI or React Native CLI
- **Package Manager:** npm or yarn
- **Version Control:** Git
- **State Management:** Redux, Context API, or Zustand
- **HTTP Client:** Axios or Fetch API
- **Local Storage:** AsyncStorage or SQLite

---

## Backend Integration

### Existing Infrastructure
✅ FastAPI server (api/main.py)  
✅ Authentication system (routes/auth.py)  
✅ User management (users.json + environment variables)  
✅ Assignment database  
✅ PDF generation (ReportLab)  

### Required API Endpoints (New)
1. `/auth/login` (exists) - Mobile authentication
2. `/auth/mobile-device-register` - Register device for push notifications
3. `/assignments/driver/{username}` - Get driver's assignments for day
4. `/assignments/{assignment_id}/status` - Update assignment status
5. `/incidents/create` - Submit incident report with photos
6. `/incidents/{incident_id}/photos` - Upload incident photos
7. `/driver/profile` - Get driver profile + assignment history
8. `/notifications/preferences` - User notification settings
9. `/handouts/{assignment_id}/download` - Get handout PDF

### Backend Enhancements Needed
- **WebSocket support** for real-time updates (optional, Phase 2)
- **File upload handling** for incident photos
- **Photo storage integration** (AWS S3 or Google Cloud)
- **Push notification service** integration
- **Offline sync** mechanism for local data caching

---

## Cloud Services Required

### 1. Push Notifications
**Service:** Firebase Cloud Messaging (FCM)
- **Cost:** Free tier (unlimited messages)
- **Setup Time:** 2-3 hours
- **Integrations:** 
  - Backend: Send notifications via FCM API
  - Mobile: Firebase libraries for message reception
- **Use Cases:**
  - Assignment notifications
  - Route updates
  - Incident acknowledgments
  - Emergency alerts

### 2. Photo Storage
**Service:** AWS S3 or Google Cloud Storage
- **Cost:** ~$0.023/GB (S3 standard)
- **Setup Time:** 2-3 hours
- **Capacity Planning:** 100 drivers × 5 photos/week × 50KB = ~2.5GB/year
- **Use Cases:**
  - Incident photos
  - Van inspection images
  - Proof of delivery
  - Driver documentation

### 3. Real-Time Data (Phase 2)
**Service:** Firebase Realtime Database or Supabase
- **Cost:** Free tier or $15-100/month
- **Use Cases:**
  - Live assignment status
  - Driver location tracking
  - Sweeper availability
  - Incident updates

### 4. Analytics (Optional)
**Service:** Firebase Analytics or Mixpanel
- **Cost:** Free tier available
- **Tracking:** App usage patterns, crash reports, engagement metrics

---

## Development Environment Setup

### Local Development Requirements
1. **Node.js** (v18 or higher)
   - `brew install node` (macOS)
   - Direct download from nodejs.org

2. **React Native CLI**
   ```
   npm install -g react-native-cli
   ```

3. **Expo CLI** (recommended for faster development)
   ```
   npm install -g expo-cli
   ```

4. **Android Development**
   - Android Studio (for emulator)
   - JDK 11 or higher
   - Android SDK (API 21+)

5. **iOS Development** (macOS only)
   - Xcode with Command Line Tools
   - CocoaPods

6. **Development Phone**
   - Android phone or simulator
   - iOS phone or simulator (macOS only)

### Testing Tools
- **Unit Testing:** Jest + React Native Testing Library
- **E2E Testing:** Detox
- **Real Device Testing:** BrowserStack or Appetize.io
- **Network Debugging:** Flipper or React Native Debugger

---

## Build & Deployment Pipeline

### Development Builds
```
expo start          # Start dev server
expo run:android    # Run on Android emulator
expo run:ios        # Run on iOS simulator
```

### Production Builds

#### Option 1: EAS Build (Recommended for Teams)
- **Service:** Expo Application Services
- **Cost:** $7-99/month depending on build concurrency
- **Setup Time:** 1 hour
- **Features:**
  - Managed build infrastructure
  - Automatic code signing
  - Build history and management
  - Email notifications on build completion

#### Option 2: Local Build Pipeline
- **Cost:** Free (infrastructure already available)
- **Setup Time:** 4-6 hours
- **Requirements:**
  - Android Studio + Xcode locally
  - CI/CD system (GitHub Actions, GitLab CI)
  - Apple Developer Account ($99/year)
  - Google Play Developer Account ($25 one-time)

### App Store Distribution
**Apple App Store:**
- Developer account: $99/year
- Review process: 1-2 days
- Minimum iOS version: 13.0

**Google Play Store:**
- Developer account: $25 one-time
- Review process: 2-4 hours
- Minimum Android version: API 21 (Android 5.0)

---

## Development Team & Timeline

### Recommended Team Composition
1. **React Native Developer** (1 FTE)
   - Mobile app development
   - Push notification integration
   - Photo upload/storage handling
   - Testing and debugging

2. **Backend Developer** (0.5 FTE)
   - New API endpoint development
   - Photo storage integration
   - Push notification setup
   - Database schema updates

3. **DevOps/Infrastructure** (0.25 FTE)
   - Cloud service setup (Firebase, S3, etc.)
   - CI/CD pipeline configuration
   - Environment management
   - Monitoring and alerting

### Timeline Estimates

| Phase | Deliverable | Duration | Effort |
|-------|-------------|----------|--------|
| **Phase 0** | Project setup, infrastructure | 3-5 days | 40 hours |
| **Phase 1** | MVP - Assignments & notifications | 2 weeks | 80 hours |
| **Phase 2** | Incident reporting, photo upload | 2 weeks | 80 hours |
| **Phase 3** | Polish, testing, beta | 1 week | 40 hours |
| **Phase 4** | App Store submission, launch | 3-5 days | 20 hours |
| **Total** | **Production Ready** | **~5-6 weeks** | **260 hours** |

### Cost Estimate

| Category | Monthly | Annual |
|----------|---------|--------|
| **Cloud Services** | | |
| Firebase (FCM, Analytics) | $0 (free tier) | $0 |
| AWS S3 for photos | $5-15 | $60-180 |
| Firebase Realtime DB (Phase 2) | $0-15 | $0-180 |
| **Build & Deployment** | | |
| EAS Build (managed) | $7-25 | $84-300 |
| Apple Dev Account | $0 | $99/year |
| Google Play Account | $0 | $25/year |
| **Developer Tools** | | |
| BrowserStack (optional) | $0-30 | $0-360 |
| **Total** | **$12-85** | **$253-1144** |

---

## MVP Features (Phase 1-2)

### Phase 1: Core Assignment App (Week 1-2)
- User authentication (login with existing credentials)
- View daily assignments
- Assignment details (route, driver, van, wave time)
- Push notifications for new assignments
- Basic profile view
- Handout PDF download/viewing

### Phase 2: Incident Reporting (Week 3-4)
- Incident report form
- Photo capture (on-device camera)
- Incident types (accident, damage, safety, customer complaint)
- GPS location tagging
- Offline queueing (submit when reconnected)
- Photo upload to S3
- Incident history view

### Phase 3: Polish & Testing (Week 5)
- UI/UX refinement
- Performance optimization
- Crash testing and bug fixes
- User documentation
- Beta testing with real drivers

### Phase 4: Launch (Week 6)
- App Store submission
- Play Store submission
- Release notes
- User onboarding messaging

---

## Data & Security Considerations

### Authentication
- Use existing FastAPI JWT tokens
- Store tokens in secure AsyncStorage (encrypted)
- Implement token refresh logic
- Auto-logout on token expiration

### Data Encryption
- HTTPS for all API communication (already enabled)
- Encrypt sensitive data in local storage
- Mask PII in logs (phone numbers, social security numbers)

### Permissions Required
- **Android:** CAMERA, INTERNET, ACCESS_FINE_LOCATION, WRITE_EXTERNAL_STORAGE
- **iOS:** NSCameraUsageDescription, NSLocationWhenInUseUsageDescription
- Implement runtime permission requests (Android 6.0+)

### Privacy Compliance
- GDPR: Data deletion requests, privacy policy
- CCPA: Opt-out options for location tracking
- Transparent photo/location usage
- User consent before collecting location

---

## Offline Capability

### Local Data Sync Strategy
- **Assigned routes:** Cached on device
- **Incident drafts:** Queue for submission when online
- **PDFs:** Downloaded for offline access
- **Detection:** Monitor network status, show offline indicator

### Sync Mechanism
1. Device detects network loss
2. Queue pending actions locally (AsyncStorage)
3. On reconnection, automatically sync
4. Show confirmation of sync status
5. Handle merge conflicts (server data wins)

---

## Performance Targets

- **App launch time:** < 3 seconds
- **Assignment list load:** < 1 second
- **PDF download:** < 2 seconds
- **Photo upload:** < 5 seconds (on 4G)
- **Map rendering:** < 1 second
- **Battery impact:** < 5% per 8-hour shift
- **Data usage:** < 50MB per day

---

## Rollout Strategy

### Beta Testing (Week 5)
- Deploy to 5-10 beta drivers
- Collect feedback and crash reports
- Fix critical issues
- Document user behaviors

### Soft Launch (Week 6)
- Release to 25% of driver population
- Monitor usage and stability
- Gather feedback through in-app surveys
- Fix any production issues

### Full Launch (Week 7)
- Gradual rollout to all drivers
- Send in-app tutorials
- Provide phone support
- Monitor metrics (adoption rate, crash rate)

### Success Metrics
- **Adoption:** >80% of drivers active within 2 weeks
- **Crash Rate:** <0.1% of sessions
- **Average Session:** >5 minutes
- **Retention:** >70% weekly active drivers
- **Feature Usage:** >60% use incident reporting

---

## Maintenance & Support Plan

### Weekly Maintenance
- Review crash reports
- Monitor Firebase metrics
- Respond to user feedback
- Plan bug fixes and patches

### Monthly Updates
- Bug fixes and performance improvements
- New features from roadmap
- Security patches
- OS compatibility updates

### Quarterly Reviews
- User feedback analysis
- Feature usage analytics
- Performance benchmarking
- Product roadmap prioritization

---

## Next Steps

1. **Week 1:** Secure approval and budget for cloud services
2. **Week 2:** Set up development environment and infrastructure
3. **Week 3:** Begin Phase 1 development (assignments app)
4. **Week 4:** Backend team creates new API endpoints
5. **Week 5-6:** Phase 2 & 3 development (incidents, polish)
6. **Week 7+:** Testing and launch

---

## Related Documents
- [UPGRADE_BACKLOG.md](UPGRADE_BACKLOG.md) - Feature prioritization and roadmap
- [VAN_INGEST_RULES.md](VAN_INGEST_RULES.md) - Fleet data standards
- [../api/README.md](../api/README.md) - Backend API documentation

---

**Approval & Sign-Off:**
- [ ] Product Owner: _______________
- [ ] Engineering Lead: _______________
- [ ] Operations: _______________

---

**Last Updated:** February 23, 2026  
**Next Review:** March 1, 2026
