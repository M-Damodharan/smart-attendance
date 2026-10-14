FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 APP_ENV=production
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -m app && mkdir -p /srv/instance && chown -R app /srv/instance
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
