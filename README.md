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

## Printing and backups

- **Print / Save as PDF:** on your character page click *Print / Save as PDF* and choose "Save as PDF" in the browser's print dialog. Only your own sheet is printed (description, traits, secrets, relationships, objectives), never GM hints. The GM can open *Print all sheets* from the master sheet to print a handout page per player (select one character to get one PDF per player).
- **Export / restore a character:** *Export character* on your character page downloads a small JSON file. In the lobby, a player without a character can use *Restore from a file...* to create one from it; *Import character...* on an existing character shows what would change before anything is saved. The GM can use *Export all characters* on the master sheet.
- **What the file contains:** name, role, description, traits and Lifepath. It never contains secrets, relationships, objectives, the killer flag, or ids. Once the game is generated only name, description and traits can be imported until the GM resets the game.
- The file format is documented in [docs/character-file-format.md](docs/character-file-format.md).

## Troubleshooting installs

`tracery==0.1.1` is source-only and builds with a legacy `setup.py`. If `pip install` fails, upgrade the build tools first: `pip install --upgrade pip setuptools wheel`. On systems with a distro-managed `wheel`, use `pip install --ignore-installed wheel setuptools` and then `pip install --no-build-isolation tracery==0.1.1`.
