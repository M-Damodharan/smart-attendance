import multiprocessing
bind = "0.0.0.0:8000"
workers = min(4, multiprocessing.cpu_count() * 2 + 1)
timeout = 60
accesslog = "-"
errorlog = "-"
