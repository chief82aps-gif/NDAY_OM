# Governance: Where We Left Off (March 1, 2026)

## Summary
This document captures the exact technical and operational state of the NDAY Route Manager system at session close, to ensure a clean and efficient restart next session.

---

## 1. Authentication & Login
- **Backend login endpoint is fully functional** (tested via PowerShell, returns valid JWT for admin/NDAY_26!).
- **Frontend login is failing with 'Failed to fetch'** due to API URL/CORS misconfiguration or browser cache.
- **.env.local** in `frontend/` was previously set to production API; must be set to `NEXT_PUBLIC_API_URL=http://127.0.0.1:8000` for local development.
- **Action for next session:**
  - Confirm `.env.local` is correct.
  - Restart frontend server after any change.
  - Hard refresh browser and use the correct port.

---

## 2. Frontend/Backend State
- **Frontend builds and runs (no JSX errors)**, but may run on ports 3000, 3002, 3003, or 3004 due to port conflicts.
- **Backend is running and listening on port 8000.**
- **All critical backend endpoints (login, audit) are operational.**

---

## 3. Known Issues & Next Steps
- **Login CORS/API URL issue is the main blocker.**
- **No backend code errors.**
- **No frontend TypeScript/JSX errors.**
- **Multiple frontend servers may be running; only one should be active.**
- **Browser cache may cause stale API URL usage.**

---

## 4. Credentials
- **Admin login:**
  - Username: `admin`
  - Password: `NDAY_26!`
- **User credentials are managed in `api/users.json`.**

---

## 5. Outstanding Todos
- [ ] Fix Cortex parsing logic and test with sample data.
- [ ] Prompt user for training data if missing.
- [ ] Scrub and prompt for Excluded Services confirmations.
- [ ] Ensure only one frontend server is running and using the correct API URL.

---

## 6. How to Resume
1. Confirm backend is running on port 8000.
2. Confirm `.env.local` in `frontend/` is set to `NEXT_PUBLIC_API_URL=http://127.0.0.1:8000`.
3. Stop all frontend servers, then run `npm run dev` in `frontend/`.
4. Use the correct port in your browser (check terminal output).
5. Hard refresh browser before login.
6. Use admin/NDAY_26! to log in.

---

## 7. Reference
- **Backend docs:** http://127.0.0.1:8000/docs
- **Frontend dev:** http://localhost:3000 (or next available port)
- **User management:** `api/users.json`
- **CORS config:** `api/main.py` and `.env.local`

---

**Session closed: March 1, 2026. Ready for clean restart next session.**
