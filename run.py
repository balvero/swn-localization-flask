"""Local dev entry point: python3 run.py
Loads .env, then starts the Flask dev server."""

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from app import create_app  # noqa: E402 — must come after load_dotenv

app = create_app()

if __name__ == "__main__":
    # threaded=True: "Draft all with AI" polls a status endpoint every
    # second while a background thread does the actual drafting — without
    # this, the single-threaded dev server would queue the poll requests
    # behind whatever's currently being served instead of answering them
    # immediately.
    app.run(port=5001, debug=True, threaded=True)
