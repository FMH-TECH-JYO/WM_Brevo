"""Entry point for the FM Invoice Readiness Mailer."""
import os
from app import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    print("=" * 58)
    print("  FM Invoice Readiness Mailer")
    print(f"  Open browser →  http://localhost:{port}")
    print(f"  Health check →  http://localhost:{port}/health")
    print("=" * 58)
    app.run(debug=debug, host="0.0.0.0", port=port)
