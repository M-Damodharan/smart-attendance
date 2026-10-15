"""SBG Smart Attendance 24/7 Watchdog Service.
Runs continuously to ensure:
1. Local server (http://127.0.0.1:5000) is running 24/7. If down, automatically starts it.
2. Cloud server (https://smart-attendance-u7e6.onrender.com) is kept awake 24/7 by pinging every 4 minutes.
"""
import os
import sys
import time
import subprocess
import urllib.request
import logging
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE = os.path.join(BASE_DIR, "instance", "watchdog_24_7.log")
os.makedirs(os.path.join(BASE_DIR, "instance"), exist_ok=True)

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
logging.getLogger("").addHandler(console)

LOCAL_HEALTH_URL = "http://127.0.0.1:5000/healthz"
CLOUD_HEALTH_URL = "https://smart-attendance-u7e6.onrender.com/healthz"

local_process = None


def check_url(url, timeout=10):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SBG-247-Watchdog/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def start_local_server():
    global local_process
    logging.info("Starting local Waitress server (python run.py)...")
    run_py = os.path.join(BASE_DIR, "run.py")
    python_exe = sys.executable
    local_process = subprocess.Popen(
        [python_exe, run_py],
        cwd=BASE_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(3)
    if check_url(LOCAL_HEALTH_URL):
        logging.info("Local server started successfully on http://127.0.0.1:5000 (PID: %s)", local_process.pid)
    else:
        logging.warning("Local server process launched (PID: %s) but not yet responding on /healthz.", local_process.pid)


def main():
    logging.info("==================================================")
    logging.info("SBG Attendance 24/7 Watchdog Started")
    logging.info("Local Target: %s", LOCAL_HEALTH_URL)
    logging.info("Cloud Target: %s", CLOUD_HEALTH_URL)
    logging.info("==================================================")

    last_cloud_ping = 0
    # Ping cloud every 240 seconds (4 minutes) to eliminate 15-minute Render sleep
    CLOUD_PING_INTERVAL = 240

    while True:
        try:
            now = time.time()

            # 1. Check Local Server
            if not check_url(LOCAL_HEALTH_URL, timeout=4):
                logging.warning("Local server is DOWN. Restarting now...")
                start_local_server()
            
            # 2. Check / Warm Cloud Server
            if now - last_cloud_ping >= CLOUD_PING_INTERVAL:
                is_cloud_ok = check_url(CLOUD_HEALTH_URL, timeout=15)
                if is_cloud_ok:
                    logging.info("Cloud server pinged: 200 OK (Warm & Active 24/7)")
                else:
                    logging.warning("Cloud server did not respond with 200 OK. Retrying in next loop...")
                last_cloud_ping = now

        except Exception as e:
            logging.error("Watchdog loop error: %s", e)

        # Poll local server every 30 seconds
        time.sleep(30)


if __name__ == "__main__":
    main()
