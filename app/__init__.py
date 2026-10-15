import os
from datetime import datetime

from flask import Flask, g, render_template, session

from config import Config


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    if not app.config.get("SECRET_KEY") or app.config["SECRET_KEY"] in ("", "change-this-secret-in-production"):
        import secrets
        app.config["SECRET_KEY"] = secrets.token_hex(32)
    # Always apply ProxyFix to accurately handle reverse proxy / tunnel headers
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)

    # Dynamic session interface: automatically applies Secure flag on HTTPS (Cloudflare tunnel / mobile)
    from flask.sessions import SecureCookieSessionInterface
    class DynamicSessionInterface(SecureCookieSessionInterface):
        def get_cookie_secure(self, app):
            from flask import request
            try:
                return request.is_secure or request.headers.get("X-Forwarded-Proto") == "https"
            except Exception:
                return False
    app.session_interface = DynamicSessionInterface()
    _setup_logging(app)

    from . import ai_assistant, api, auth, db, employee, hr
    from .services import STATUS_LABELS, today

    db.init_db(app)
    app.teardown_appcontext(db.close_db)
    app.before_request(auth.load_user)
    app.before_request(auth.check_csrf)

    for m in (auth, employee, hr, api, ai_assistant):
        app.register_blueprint(m.bp)

    @app.context_processor
    def inject():
        return {"csrf_token": auth.csrf_token, "STATUS_LABELS": STATUS_LABELS, "today": today()}

    @app.template_filter("hm")
    def hm(v):  # "2026-10-14 14:21:00" -> "02:21 PM"
        if not v:
            return "—"
        try:
            if isinstance(v, str) and len(v) >= 16:
                dt = datetime.strptime(v[:19], "%Y-%m-%d %H:%M:%S") if len(v) >= 19 else datetime.strptime(v[:16], "%Y-%m-%d %H:%M")
                return dt.strftime("%I:%M %p")
            if isinstance(v, str) and ":" in v:
                parts = v.strip().split(":")
                h, m = int(parts[0]), int(parts[1])
                sfx = "PM" if h >= 12 else "AM"
                h12 = h % 12 or 12
                return f"{h12:02d}:{m:02d} {sfx}"
            return v
        except Exception:
            return v[11:16] if len(v) >= 16 else v

    @app.template_filter("hm24")
    def hm24(v):  # "2026-10-14 14:21:00" -> "14:21"
        return v[11:16] if (v and len(v) >= 16) else (v or "—")

    @app.template_filter("dmy")
    def dmy(v):
        try:
            d = v if hasattr(v, "strftime") else datetime.strptime(v[:10], "%Y-%m-%d")
            return d.strftime("%d %b %Y")
        except Exception:
            return v or ""

    @app.template_filter("hrs")
    def hrs(v):
        if v is None or v == "":
            return "—"
        try:
            from .services import format_duration
            return format_duration(float(v))
        except Exception:
            return "—"

    @app.route("/sw.js")
    def service_worker():
        from flask import send_from_directory
        return send_from_directory(app.static_folder + "/js", "sw.js", mimetype="application/javascript")

    @app.route("/download/apk")
    def download_apk():
        import os
        from flask import send_from_directory
        dl_dir = os.path.join(app.static_folder, "downloads")
        return send_from_directory(dl_dir, "SBG.apk", as_attachment=True, download_name="SBG.apk", mimetype="application/vnd.android.package-archive")

    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Permissions-Policy"] = "camera=(self), geolocation=(self)"
        return resp

    @app.route("/healthz")
    def healthz():
        db.get_db().execute("SELECT 1")
        return {"status": "ok"}

    @app.errorhandler(500)
    def server_error(e):
        app.logger.exception("Unhandled error")
        return render_template("error.html", e=type("E", (), {"code": 500, "description": "Something went wrong. Please try again."})()), 500

    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(400)
    def err(e):
        return render_template("error.html", e=e), e.code

    if not app.config.get("TESTING") and (os.environ.get("APP_ENV") == "production" or os.environ.get("RENDER")):
        _start_keep_alive(app)

    return app


def _start_keep_alive(app):
    """Periodically pings cloud health endpoint to prevent Render free-tier idle spin-down."""
    import os
    import threading
    import time
    import urllib.request

    ext_url = os.environ.get("RENDER_EXTERNAL_URL") or "https://smart-attendance-u7e6.onrender.com"
    target = f"{ext_url.rstrip('/')}/healthz"

    def _loop():
        time.sleep(30)
        while True:
            try:
                req = urllib.request.Request(target, headers={"User-Agent": "SBG-Warmup/1.0"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    if r.getcode() == 200:
                        app.logger.info("Render keepalive ping successful: 200 OK")
            except Exception as e:
                app.logger.debug("Render keepalive ping notice: %s", e)
            # Sleep 8 minutes (Render free tier spins down after 15 minutes of inactivity)
            time.sleep(480)

    t = threading.Thread(target=_loop, daemon=True, name="sbg-render-warmup")
    t.start()


def _setup_logging(app):
    import logging
    from logging.handlers import RotatingFileHandler
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    h = logging.StreamHandler()
    h.setFormatter(fmt)
    app.logger.addHandler(h)
    try:
        fh = RotatingFileHandler(app.config["LOG_FILE"], maxBytes=2_000_000, backupCount=5)
        fh.setFormatter(fmt)
        app.logger.addHandler(fh)
    except OSError:
        pass
    app.logger.setLevel(logging.INFO)
