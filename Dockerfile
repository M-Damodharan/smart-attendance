FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 APP_ENV=production SECRET_KEY=sbg-auto-secret-key-32-chars-long-balaji ADMIN_CODE=HR001 ADMIN_PASSWORD=damo12345 TURSO_DATABASE_URL=sbg-attendance-m-damodharan.aws-ap-south-1.turso.io TURSO_AUTH_TOKEN=eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicnciLCJpYXQiOjE3OTMwODYwOTcsImlkIjoiMDFhMTdjNDItNDEwMS03ZjA0LWE2OWUtODgyYTgyYjRiYTQ3Iiwia2lkIjoiOE5CcW9uNmNDMmZHR1ZMM2NLZ3FNd2pqZzd6NWJOaWFUTGRaei0tTlZXWSIsInJpZCI6IjI3OGU4NGU1LTliNmMtNDU5NC1hYjJkLTcxM2VlYjU5ZjZmZSJ9.dplC14DykiOUJ12lI07b6owuv3pdxGmyMRK_nedKJbSGXzcjePrXJtT6N-8c2U1yO7b3hcNclZ569nbkiMkiAA

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -m app && mkdir -p /srv/instance && chown -R app:app /srv
USER app
EXPOSE 8000 10000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request, os; p = os.environ.get('PORT', '8000'); urllib.request.urlopen(f'http://127.0.0.1:{p}/healthz')"
CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]

