import os
import multiprocessing

port = os.environ.get("PORT", "8000")
bind = f"0.0.0.0:{port}"

# Load balancing & concurrency settings
# Default to at least 2 workers or CPU count + 1
default_workers = max(2, min(4, multiprocessing.cpu_count()))
workers = int(os.environ.get("WEB_CONCURRENCY") or os.environ.get("WORKERS") or default_workers)
worker_class = "gthread"
threads = int(os.environ.get("THREADS", 8))

# Connection management
backlog = 2048
timeout = 120
keepalive = 75

# Auto-recycling to keep memory lean under sustained heavy load
max_requests = 1000
max_requests_jitter = 50

preload_app = True
accesslog = "-"
errorlog = "-"


