"""Run the Smart Attendance app.

    python run.py            -> High-concurrency Waitress WSGI server (16 multi-threaded workers, load-balanced connection queue)
    python run.py --dev      -> Flask development server
    python run.py --https    -> Self-signed HTTPS for local Wi-Fi testing
"""
import os
import sys
import logging
from app import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    if "--https" in sys.argv:
        app.run(host="0.0.0.0", port=port, debug=False, ssl_context="adhoc", threaded=True)
    elif "--dev" in sys.argv:
        app.run(host="0.0.0.0", port=port, debug=True, threaded=True)
    else:
        try:
            from waitress import serve
            logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
            logger = logging.getLogger("waitress")
            logger.setLevel(logging.INFO)
            print("==================================================================")
            print("  SBG Smart Attendance — Production WSGI Server (Waitress)")
            print("  Threads: 16 Multi-Threaded Workers (Load Balanced)")
            print("  Connection Limit: 1000 Concurrent Connections")
            print(f"  Listening on: http://0.0.0.0:{port}")
            print("==================================================================")
            serve(
                app,
                host="0.0.0.0",
                port=port,
                threads=16,
                connection_limit=1000,
                channel_timeout=120,
                cleanup_interval=30
            )
        except ImportError:
            print("[SBG] Waitress not installed, using threaded Flask server...")
            app.run(host="0.0.0.0", port=port, debug=False, threaded=True)

