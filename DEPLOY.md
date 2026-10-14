# Deploying for real (HTTPS server)

You need: a Linux server (any 1 GB VPS is enough for ~100–300 staff), a domain name pointing to its IP, Docker installed.

```bash
# 1. copy the project to the server, then:
cd smart-attendance
cp .env.example .env && nano .env        # set DOMAIN, SECRET_KEY, ADMIN_* values
docker compose up -d --build             # app + automatic HTTPS (ports 80/443 must be open)
docker compose logs -f app               # should show "HR admin ... created"
```
Open `https://YOUR-DOMAIN`, sign in with ADMIN_CODE / ADMIN_PASSWORD, set a new password, then:

1. **Settings → Offices**: paste your Google Maps link, set the radius.
2. **Settings → Shifts / Holidays / Leave types**: match your policy.
3. **Employees → Add**: create each employee (temporary password; they must change it at first login).
4. Employees open the site on their phone → browser menu → *Add to Home screen*.

## Without Docker
```bash
pip install -r requirements.txt
export APP_ENV=production SECRET_KEY=... ADMIN_CODE=HR001 ADMIN_PASSWORD=... 
gunicorn -c gunicorn.conf.py wsgi:app      # put nginx/Caddy with HTTPS in front
```

## Operations
* **Backups:** `./backup.sh` (cron daily). Restore = copy `attendance.db` and `selfies/` back into `instance/`.
* **Forgot password:** HR → Employees → Edit → Reset password, or `python manage.py reset-password CODE`.
* **Logs:** `instance/app.log` (rotating) and `docker compose logs`.
* **Health check:** `GET /healthz`.
* **Update:** replace the code, `docker compose up -d --build` (the database lives in `instance/`, it is kept).
* **Scale:** SQLite (WAL mode) comfortably handles a few hundred staff punching in the same minute. For thousands, move to PostgreSQL.
