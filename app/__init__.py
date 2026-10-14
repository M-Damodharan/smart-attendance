from datetime import datetime

from flask import Flask, g, render_template, session

from config import Config


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    if app.config["PRODUCTION"] and app.config["SECRET_KEY"] in ("", "change-this-secret-in-production"):
        raise RuntimeError("Set a strong SECRET_KEY environment variable in production.")
    if app.config["BEHIND_PROXY"]:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    _setup_logging(app)

    from . import api, auth, db, employee, hr
    from .services import STATUS_LABELS, today

    db.init_db(app)
    app.teardown_appcontext(db.close_db)
    app.before_request(auth.load_user)
    app.before_request(auth.check_csrf)

    for m in (auth, employee, hr, api):
        app.register_blueprint(m.bp)

    @app.context_processor
    def inject():
        return {"csrf_token": auth.csrf_token, "STATUS_LABELS": STATUS_LABELS, "today": today()}

    @app.template_filter("hm")
    def hm(v):  # "2026-10-14 09:03:11" -> "09:03"
        return v[11:16] if v else "—"

    @app.template_filter("dmy")
    def dmy(v):
        try:
            d = v if hasattr(v, "strftime") else datetime.strptime(v[:10], "%Y-%m-%d")
            return d.strftime("%d %b %Y")
        except Exception:
            return v or ""

    @app.template_filter("hrs")
    def hrs(v):
        if not v:
            return "—"
        h, m = divmod(int(round(v * 60)), 60)
        return f"{h}h {m:02d}m"

    @app.route("/sw.js")
    def service_worker():
        from flask import send_from_directory
        return send_from_directory(app.static_folder + "/js", "sw.js", mimetype="application/javascript")

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

    return app


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
