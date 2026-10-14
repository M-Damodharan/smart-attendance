#!/bin/sh
# Daily backup (add to cron:  0 2 * * * /path/to/smart-attendance/backup.sh)
cd "$(dirname "$0")"
docker compose exec -T app python manage.py backup /srv/instance/backups 2>/dev/null \
  || python manage.py backup backups
# keep last 14 backups
ls -dt instance/backups/*/ backups/*/ 2>/dev/null | tail -n +15 | xargs -r rm -rf
