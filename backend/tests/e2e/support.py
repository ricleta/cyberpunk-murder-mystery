"""Helpers and fixtures shared by the browser tests (imported by the test modules; a second
conftest.py would clash with tests/conftest.py)."""
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request

import pytest

PASSWORD = 'correct-horse-battery'
XSS_NAME = '<img src=x onerror=window.__xss=1>'


class Player:
    """One signed-in browser context that records JavaScript errors."""

    def __init__(self, browser, servers, name):
        self.name = name
        self.web = servers['web']
        self.api_base = servers['api']
        self.context = browser.new_context(viewport={'width': 1200, 'height': 900}, accept_downloads=True)
        self.page = self.context.new_page()
        self.js_errors = []
        self.page.on('pageerror', lambda e: self.js_errors.append(f'{name}: pageerror {e}'))
        # Chromium also logs every 4xx response as a console "error"; those are not script
        # errors (a new player's GET characters/mine is a 404 by design), so skip them.
        self.page.on('console', lambda m: self.js_errors.append(f'{name}: console {m.text}')
                     if m.type == 'error' and not m.text.startswith('Failed to load resource') else None)

    def goto(self, path):
        self.page.goto(f'{self.web}/{path}')

    def signup(self):
        self.goto('index.html')
        self.page.click('#showSignup')
        self.page.fill('#signupUsername', self.name)
        self.page.fill('#signupPassword', PASSWORD)
        self.page.click('#signupSubmit')
        self.page.wait_for_url('**/dashboard.html')

    @property
    def token(self):
        return self.page.evaluate("localStorage.getItem('token')")

    def api(self, path, method='GET'):
        request = urllib.request.Request(f'{self.api_base}{path}', method=method,
                                         headers={'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/json'},
                                         data=b'{}' if method != 'GET' else None)
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())

    def join(self, join_code):
        self.page.fill('#joinCode', join_code)
        self.page.click('#joinSubmit')
        self.page.wait_for_url('**/lobby.html?id=*')


def finish_lifepath(player):
    """Rolls every dropdown and fills the two faction names, waiting for each save."""
    page = player.page
    page.wait_for_selector('#builderContent:not([hidden])')
    rolls = page.locator('.roll-btn')
    for i in range(rolls.count()):
        with page.expect_response(lambda r: r.request.method == 'PATCH'):
            rolls.nth(i).click()
    names = page.locator('.faction-columns input[type="text"]')
    for i in range(names.count()):
        with page.expect_response(lambda r: r.request.method == 'PATCH'):
            names.nth(i).fill(f'Faction {i} of {player.name}')
    page.wait_for_function("document.getElementById('completionTracker').textContent.includes('READY')")


def create_character(player, name, role):
    player.page.fill('#myCharName', name)
    player.page.select_option('#myRoleSelect', role)
    player.page.click('#createCharacterForm button[type="submit"]')
    player.page.wait_for_url('**/build.html?*')


BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
FRONTEND = os.path.abspath(os.path.join(BACKEND, '..', 'frontend'))
API_PORT = 5000


def port_is_free(port):
    with socket.socket() as s:
        return s.connect_ex(('127.0.0.1', port)) != 0


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture(scope='module')
def servers(tmp_path_factory):
    if not port_is_free(API_PORT):
        pytest.skip(f'Port {API_PORT} is in use; stop the dev server to run the browser tests.')

    sys.path.insert(0, BACKEND)
    from werkzeug.serving import make_server
    from app import create_app
    from config import Config
    from extensions import db

    db_path = tmp_path_factory.mktemp('e2e') / 'e2e.db'

    class E2EConfig(Config):
        TESTING = True
        SQLALCHEMY_DATABASE_URI = 'sqlite:///' + str(db_path).replace('\\', '/')
        JWT_SECRET_KEY = 'e2e-secret'

    app = create_app(E2EConfig)
    with app.app_context():
        db.create_all()
    api = make_server('127.0.0.1', API_PORT, app, threaded=True)
    threading.Thread(target=api.serve_forever, daemon=True).start()

    web_port = free_port()
    web = subprocess.Popen([sys.executable, '-m', 'http.server', str(web_port), '--bind', '127.0.0.1', '--directory', FRONTEND],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 10
    while time.time() < deadline and port_is_free(web_port):
        time.sleep(0.1)
    try:
        yield {'web': f'http://localhost:{web_port}', 'api': f'http://localhost:{API_PORT}/api'}
    finally:
        web.terminate()
        api.shutdown()
        api.server_close()


@pytest.fixture(scope='module')
def browser():
    sync_api = pytest.importorskip(
        'playwright.sync_api',
        reason='Playwright is not installed: pip install -r requirements-dev.txt && playwright install chromium')
    with sync_api.sync_playwright() as p:
        try:
            chromium = p.chromium.launch()
        except Exception as e:  # browser binary missing
            pytest.skip(f'Chromium is not installed ({str(e).splitlines()[0]}). Run: playwright install chromium')
        yield chromium
        chromium.close()
