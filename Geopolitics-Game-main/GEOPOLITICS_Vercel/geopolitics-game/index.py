"""Vercel entrypoint: Vercel's Python runtime looks for a Flask instance named `app` in index.py.
Locally you can still use `python run.py`."""
from dotenv import load_dotenv

load_dotenv()
from app import create_app  # noqa: E402

app = create_app()
