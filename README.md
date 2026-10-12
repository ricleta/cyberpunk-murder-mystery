# Cyberpunk Murder Mystery

A web app for running a cyberpunk murder-mystery game night. Players build characters with a Lifepath; the GM generates relationships, secrets and point-valued objectives in one step. Each player sees only their own sheet; the GM sees everything. Flask + SQLAlchemy backend, vanilla-JS static frontend.

## Local development

```bash
cd backend
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python init_db.py                                  # or: flask --app app db upgrade
flask --app app run --debug --port 5000
python -m http.server 8000 --directory ../frontend # then open http://localhost:8000
```

The frontend talks to `http://localhost:5000/api` on localhost (see `frontend/js/config.js`).

## Tests

```bash
cd backend
pytest                      # everything, including the browser smoke test if Playwright is installed
pytest -m "not e2e"         # backend only
playwright install chromium # one-time, for tests/e2e (needs port 5000 free)
pytest tests/e2e
```

## Layout

- `backend/app.py`, `config.py` - app factory, startup checks
- `backend/routes/` - session, character, generation and content endpoints
- `backend/services/` - validation, serializers (name resolution), locks, cleanup, master sheet
- `backend/generation/` - relationships, secrets, objectives, compose, name tokens
- `backend/migrations/` - Alembic
- `frontend/` - pages, `js/` (dom/ui helpers, per-page modules), `data/` (source of truth for JSON content; copy with `backend/copy_data.py`)

See `backend/DEPLOY.md` for deployment.
