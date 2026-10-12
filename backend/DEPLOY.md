# Deployment Guide

A Flask backend (PythonAnywhere) and a static HTML/JS frontend (GitHub Pages).

## 1. Environment variables

The app **refuses to start** outside debug/testing mode unless all of these are set:

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Flask secret. Must not be the built-in default. |
| `JWT_SECRET_KEY` | Signs login tokens. Must not be the built-in default. |
| `CORS_ORIGINS` | Comma-separated allowed browser origins, e.g. `https://you.github.io` (no path, no trailing slash). `*` is refused. |
| `DATABASE_URL` | Optional. Defaults to `sqlite:///cyberpunk.db` (stored in `instance/`). |

In debug/testing the secrets fall back to dev defaults and CORS allows `localhost` / `127.0.0.1` on any port.

## 2. Backend (PythonAnywhere)

1. Upload or clone the repository so `backend/` sits at e.g. `/home/you/backend`. The backend reads **only `backend/data`** at runtime.
2. Create a virtualenv (Python 3.10+; tested on 3.12 and 3.14) and install:
   `pip install -r requirements.txt`.
   `tracery==0.1.1` is source-only on PyPI. If its install fails with "No module named setuptools" or "bdist_wheel", run `pip install --upgrade pip setuptools wheel` first.
3. Database:
   - New database: `flask --app app db upgrade` (the migration chain builds a fresh database). `python init_db.py` + `flask --app app db stamp head` also works.
   - Existing database: `flask --app app db upgrade`. The latest revision adds `session.generation_seed/generated_at/generation_report` and drops the unused `character.objectives` column.
4. Web tab: manual configuration, virtualenv path, source directory `/home/you/backend`. WSGI file:
   ```python
   import sys, os
   path = '/home/you/backend'
   if path not in sys.path:
       sys.path.append(path)
   os.environ['SECRET_KEY'] = '...long random string...'
   os.environ['JWT_SECRET_KEY'] = '...another long random string...'
   os.environ['CORS_ORIGINS'] = 'https://YOUR-USERNAME.github.io'
   from app import create_app
   application = create_app()
   ```
5. Reload the web app. Unexpected errors are logged with tracebacks to the server error log; clients only see a generic JSON 500.

## 3. Template data

`frontend/data/` is the source of truth. After editing any JSON there, run `python copy_data.py` from `backend/` and commit both copies. Templates (secrets, objectives, compose) are validated at startup and the app will not start if they are invalid.

## 4. Frontend (GitHub Pages)

1. Edit `PRODUCTION_API_BASE` in `frontend/js/config.js` to `https://<you>.pythonanywhere.com/api` **before deploying**. It is used on every host except `localhost` / `127.0.0.1`.
2. Publish the contents of `frontend/`. Project sites are served under `/repo-name/`; all links and redirects are relative, so no other change is needed.

## 5. Existing sessions and name tokens

Generated text now stores `{{c:<character id>}}` tokens and resolves names when served, so renames show everywhere. Sessions generated before this change keep their baked-in names; **regenerate (or reset and generate) them** to benefit.

## 6. Troubleshooting

- `Refusing to start: SECRET_KEY is still the built-in development default` / `JWT_SECRET_KEY ...` - set the variable.
- `Refusing to start: CORS_ORIGINS is not set` - set your GitHub Pages origin.
- `Required data file is missing` - run `python copy_data.py` and deploy `backend/data`.
- `Template ... has unresolved placeholder` / `composeTemplates.json: ...` - a data file is invalid; the message names it.
- Browser shows CORS errors - `CORS_ORIGINS` must match the page origin exactly.
