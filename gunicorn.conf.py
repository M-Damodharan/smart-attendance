import os
import multiprocessing

port = os.environ.get("PORT", "8000")
bind = f"0.0.0.0:{port}"
workers = int(os.environ.get("WEB_CONCURRENCY", 2))
threads = 4
timeout = 60
accesslog = "-"
errorlog = "-"

