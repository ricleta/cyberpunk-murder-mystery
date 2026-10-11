# Deployment Guide (Phase 1)

This project consists of a Flask backend and a static HTML/JS frontend. It is designed to be hosted on PythonAnywhere (backend) and GitHub Pages (frontend).

## 1. Backend Deployment (PythonAnywhere)

1. **Upload Source Code**: 
   - Zip the `backend` folder and upload it to your PythonAnywhere files, or clone your repository directly via the bash console.
   - Extract it so it sits at `/home/yourusername/backend`.

2. **Virtual Environment**:
   - Open a Bash console in PythonAnywhere.
   - Run `mkvirtualenv --python=/usr/bin/python3.10 my-env` (replace python version if needed).
   - `pip install -r /home/yourusername/backend/requirements.txt`

3. **Database Initialization**:
   - In the console, export your environment variables if needed (e.g., `export FLASK_APP=backend.app:create_app`).
   - Run `flask db init` (if migrations not initialized), `flask db migrate`, `flask db upgrade` to create the SQLite file. (Note: in this specific Phase 1 code, we haven't strictly created the migrate scripts yet, so you can alternatively run a small script calling `db.create_all()` within the app context).

   - **Upgrading an existing database** (e.g. to Phase 5, which adds the `objective` table): run `flask --app app db upgrade`. A brand-new database is created with `python init_db.py` and then marked current with `flask --app app db stamp head`; the migration chain cannot build a database from empty.
   - **Template data**: `frontend/data/` is the source of truth. After changing any JSON there (including `frontend/data/objectives/`), run `python copy_data.py` from `backend/` and deploy both copies. The app validates secret and objective templates at startup and refuses to start if they are invalid.

4. **Web App Configuration**:
   - Go to the "Web" tab. Add a new web app. Choose "Manual configuration" and select the Python version matching your virtual environment.
   - Under the **Virtualenv** section, enter the path to your env (e.g., `/home/yourusername/.virtualenvs/my-env`).
   - Under the **Code** section, set the source code directory to `/home/yourusername/backend`.
   - Edit the **WSGI configuration file** to look like this:
     ```python
     import sys
     import os

     path = '/home/yourusername/backend'
     if path not in sys.path:
         sys.path.append(path)

     # Set environment variables for production
     os.environ['SECRET_KEY'] = 'your-secure-secret'
     os.environ['JWT_SECRET_KEY'] = 'your-secure-jwt-secret'
     
     from app import create_app
     application = create_app()
     ```
   - Reload the web app. Your API is now live at `https://yourusername.pythonanywhere.com`.

## 2. Frontend Deployment (GitHub Pages)

1. **Update API URL**:
   - In `frontend/js/api.js`, change `const API_BASE = '/api';` to point to your PythonAnywhere domain: 
     `const API_BASE = 'https://yourusername.pythonanywhere.com/api';`

2. **Deploy**:
   - Push the contents of the `frontend` directory to a GitHub repository.
   - Go to the repository settings -> Pages.
   - Set the source to the branch containing your frontend code (e.g., `main` root directory).
   - GitHub will provide a URL (e.g., `https://yourusername.github.io/repo-name/`).

3. **CORS Configuration (Backend)**:
   - Make sure your Flask CORS setup in `backend/app.py` allows your GitHub Pages URL in production, so requests aren't blocked by the browser. For phase 1, it is wide open by default, but should be restricted in future phases.
