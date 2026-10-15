FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 APP_ENV=production SECRET_KEY=sbg-auto-secret-key-32-chars-long-balaji ADMIN_CODE=HR001 ADMIN_PASSWORD=123456789
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -m app && mkdir -p /srv/instance && chown -R app:app /srv
USER app
EXPOSE 8000 10000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request, os; p = os.environ.get('PORT', '8000'); urllib.request.urlopen(f'http://127.0.0.1:{p}/healthz')"
CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]

