"""Run the backend locally for AutoML Autopilot without Docker.

SQLite + a local data directory; Redis/MLflow are optional for Autopilot (the
rate limiter and drift loop degrade gracefully when Redis is absent).

    python scripts/dev_autopilot_backend.py      # http://localhost:8000/docs
"""
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
os.chdir(BACKEND)
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./autopilot_dev.db")
os.environ.setdefault("DATA_DIR", str(BACKEND / ".autopilot_data"))
os.environ.setdefault("DEBUG", "false")


def _load_root_env() -> None:
    """Pick up provider keys from the repo-root .env (settings read backend/.env).

    Values already in the environment, including the SQLite defaults above, win.
    """
    env = BACKEND.parent / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_root_env()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))
