"""Streamlit Cloud entry point.

Streamlit Cloud looks for a top-level Python file by default. We keep
the real app in ``ui/app.py`` (alongside ``ui/tools.py`` and the rest
of the dashboard package) and re-export it from here so deployments
can point straight at this file without touching the project layout.

When you deploy on Streamlit Cloud:
    Main file path: streamlit_app.py
    Branch:         main
    Python:         3.11

The repository's heavy training pipeline (LightGBM / CatBoost / torch /
transformers) lives in scheduled GitHub Actions workflows, NOT in this
Streamlit app. The dashboard only reads pre-computed parquets / JSON
written by those workflows, so the runtime memory footprint stays
inside Streamlit Cloud's free-tier 1 GB limit.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make sure the project root is on sys.path so ``import config.*``,
# ``import brain.*`` and friends resolve when Streamlit Cloud runs us
# from this file directly.
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Importing ui.app at module load runs the dashboard.
# (ui/app.py calls ``main()`` at module level via its
# ``if __name__ == "__main__"`` guard; running it as a module here
# keeps that behaviour intact.)
from ui import app as _app  # noqa: E402,F401  -- side-effect import

if hasattr(_app, "main"):
    _app.main()
