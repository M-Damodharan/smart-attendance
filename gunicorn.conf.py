import os
import multiprocessing

port = os.environ.get("PORT", "8000")
bind = f"0.0.0.0:{port}"
workers = min(4, multiprocessing.cpu_count() * 2 + 1)
timeout = 60
accesslog = "-"
errorlog = "-"
