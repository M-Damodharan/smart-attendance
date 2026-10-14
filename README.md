# Smart Attendance System

Selfie + liveness + GPS-geofence attendance app with Employee and HR portals, leave / on-duty approvals,
HR corrections, audit log, monthly reports and Excel export. Mobile-first, installable as a PWA.

Stack: **Python 3.10+ · Flask · SQLite · vanilla JS** (no build step).

## Quick start

```bash
cd smart-attendance
python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
python run.py                                         # http://localhost:5000
```

| Role     | Code  | Password   |
|----------|-------|------------|
| HR       | HR001 | admin123 (forced change on first login) |
| Employee | E001–E004 | emp123 |

### Test on a phone
Browsers only allow camera + GPS on **HTTPS** (or localhost). On the same Wi-Fi run:

```bash
python run.py --https      # https://<your-pc-ip>:5000  (accept the self-signed certificate warning)
```
For production put it behind nginx/Caddy with a real certificate and run with gunicorn/waitress, e.g.
`SECRET_KEY=... SESSION_COOKIE_SECURE=1 gunicorn -w 2 "app:create_app()"`.

## First thing HR must do
**Settings → Offices & geofence**: the demo office is a placeholder (12.9716, 77.5946). Paste your Google Maps link
(or `lat,lng`) into **Set from link**, or stand at the office and press **Use my current location**, then set the radius.
Short `maps.app.goo.gl` links are resolved by the server, so it needs internet access for that. Then adjust shifts, holidays and leave quotas, and add your employees in **Employees**.

## How it maps to the flow diagram

| Diagram box | Where |
|---|---|
| Employee login / HR login | `/login` (role decides dashboard) |
| Selfie / Liveness | `static/js/punch.js` (2 random prompts, 3 frames) + `app/api.py` (single-use challenge, server motion check) |
| GPS / Geofence | `/api/geo-check`, `/api/punch` – Haversine distance vs office radius, checked **server-side** |
| Check-in → Working hours → Check-out | `app/api.py` `punch()` |
| Auto status P / HD / A / OT | `services.compute_status()` – rules editable per shift in Settings |
| Leave / OD → HR approval | `app/employee.py` + `/hr/approvals`; approved OD lets the employee punch outside the geofence that day |
| Employee master | `/hr/employees` |
| Shift 1 / Shift 2, Present / Absent | `/hr/attendance` (daily register grouped by shift, with selfie + map links) |
| Corrections → Audit log | `/hr/corrections` (reason mandatory) → `/hr/audit` |
| Monthly reports → Excel export | `/hr/reports` → `/hr/reports/export` (4 sheets: Summary, Day-wise grid, Punch Details, Leave & OD) |

## User access (HR only)
There is no self-registration. Only HR can **add** users (Employees → Add), **revoke / grant access** (blocks login without
losing history) and **delete** a user permanently (type the code to confirm; removes their records and selfies).
HR cannot delete or block their own account, nor delete the last active HR user. Every action is audit-logged.

## Status rules (per shift, editable)
Worked hours = check-out − check-in (server clock).

| Worked hours | Status |
|---|---|
| < Half-day hours (4) | **A** |
| ≥ 4 and < Full hours (8) | **HD** |
| ≥ 8 | **P** |
| ≥ Full + OT-min (8 + 1) | **OT** (OT hours = worked − 8) |

Late = check-in after shift start + grace. Other day codes: **L** leave, **HL** half-day leave, **OD**, **H** holiday,
**WO** weekly off (Sunday, see `WEEKLY_OFF` in `config.py`), **MP** missed punch (checked in, never checked out – counts
as 0 until HR corrects it), and **A** for a past working day with no record.
Payable days = P + OT + OD + L + H + WO + 0.5×HD + 0.5×HL.

## Security built in
Hashed passwords, forced password change for new/reset users, login throttling, CSRF tokens on every POST, server-side
time (client clock is ignored), single-use liveness tokens, selfies re-encoded (EXIF stripped) and served only to HR or
the owner, audit log of every login, punch, approval, correction, export and settings change.

## Honest limits of the liveness / GPS checks
* The built-in liveness is a **basic anti-spoof**: random prompts + frame-motion analysis + optional browser
  FaceDetector. It stops a photo held still or replayed tokens, but not a determined attacker with a video.
  For stricter needs, plug a face-match / liveness service (AWS Rekognition, Azure Face, face-api.js) into
  `api.punch()` where `motion_score()` is called.
* Browser GPS can be spoofed by "mock location" apps. Combine with selfie review (HR sees every selfie + map link),
  `MAX_GPS_ACCURACY_M`, and, if you need more, a native app with mock-location detection.

## Config (`config.py`)
`WEEKLY_OFF`, `MAX_GPS_ACCURACY_M`, `GEOFENCE_TOLERANCE_M`, `CHALLENGE_TTL_S`, `MIN_MOTION_SCORE`,
`REQUIRE_LIVENESS_MOTION`, `MAX_LOGIN_ATTEMPTS`. Env vars: `SECRET_KEY`, `DATABASE`, `SELFIE_DIR`, `SESSION_COOKIE_SECURE`.

## Tests
`python tests/test_e2e.py` – runs login, CSRF, geofence, liveness, punch in/out, OT status, leave/OD approval,
corrections, audit, reports and Excel export against a temporary database.

## Backup
Everything lives in `instance/` (`attendance.db` + `selfies/`). Back that folder up.

## Structure
```
run.py  config.py  requirements.txt
app/  __init__.py  db.py  auth.py  api.py  employee.py  hr.py  services.py  exports.py
      templates/ (base, login, emp/*, hr/*)   static/ (css, js, manifest, icon)
tests/test_e2e.py
```
