"""Run the Smart Attendance app.

    python run.py            -> http://localhost:5000   (camera/GPS work on localhost)
    python run.py --https    -> https://<your-ip>:5000  (self-signed cert, needed to
                                test camera + GPS from a phone on the same Wi-Fi)
"""
import sys
from app import create_app

app = create_app()

if __name__ == "__main__":
    ssl_ctx = "adhoc" if "--https" in sys.argv else None
    app.run(host="0.0.0.0", port=5000, debug=False, ssl_context=ssl_ctx)
