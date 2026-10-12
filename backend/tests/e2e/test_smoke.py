"""Browser smoke test: the whole game through the real UI.

Runs only where Playwright and a Chromium build are installed:

    pip install -r requirements-dev.txt
    playwright install chromium
    pytest tests/e2e -m e2e

It starts the Flask app (testing config, temporary SQLite file) on port 5000 - the port the
frontend's config.js uses on localhost - and serves frontend/ with `python -m http.server`
on another port.
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request

import pytest

pytestmark = pytest.mark.e2e

sync_api = pytest.importorskip(
    "playwright.sync_api",
    reason="Playwright is not installed: pip install -r requirements-dev.txt && playwright install chromium")

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
FRONTEND = os.path.abspath(os.path.join(BACKEND, '..', 'frontend'))
API_PORT = 5000
XSS_NAME = '<img src=x onerror=window.__xss=1>'
RENAMED = 'Renamed Hero'
PASSWORD = 'correct-horse-battery'


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
        pytest.skip(f'Port {API_PORT} is in use; stop the dev server to run the browser smoke test.')

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


@pytest.fixture(scope='module')
def browser():
    with sync_api.sync_playwright() as p:
        try:
            chromium = p.chromium.launch()
        except Exception as e:  # browser binary missing
            pytest.skip(f'Chromium is not installed ({str(e).splitlines()[0]}). Run: playwright install chromium')
        yield chromium
        chromium.close()


class Player:
    """One signed-in browser context that records JavaScript errors."""

    def __init__(self, browser, servers, name):
        self.name = name
        self.web = servers['web']
        self.api_base = servers['api']
        self.context = browser.new_context(viewport={'width': 1200, 'height': 900})
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

    def api(self, path):
        request = urllib.request.Request(f'{self.api_base}{path}', headers={'Authorization': f'Bearer {self.token}'})
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())


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


def test_full_game_through_the_browser(servers, browser):
    gm = Player(browser, servers, 'gm_user')
    players = [Player(browser, servers, f'player_{i}') for i in range(4)]
    everyone = [gm] + players
    try:
        # --- GM signs up and creates the session ---------------------------------------------------
        gm.signup()
        gm.page.fill('#sessionName', 'E2E Session')
        gm.page.click('#createSubmit')
        gm.page.wait_for_url('**/lobby.html?id=*')
        session_id = gm.page.url.split('id=')[1]
        gm.page.wait_for_function("document.getElementById('joinCodeDisplay').textContent !== '---'")
        join_code = gm.page.locator('#joinCodeDisplay').inner_text()
        assert len(join_code) == 6

        # --- Four players join (the generator needs at least four characters) ----------------------
        roles = ['solo', 'netrunner', 'tech', 'exec']
        for i, player in enumerate(players):
            player.signup()
            player.page.fill('#joinCode', join_code)
            player.page.click('#joinSubmit')
            player.page.wait_for_url('**/lobby.html?id=*')
            character_name = XSS_NAME if i == 0 else f'Hero {i}'
            player.page.fill('#myCharName', character_name)
            player.page.select_option('#myRoleSelect', roles[i])
            player.page.click('#createCharacterForm button[type="submit"]')
            player.page.wait_for_url('**/build.html?*')
            finish_lifepath(player)

        # --- GM generates -----------------------------------------------------------------------------
        gm.page.reload()
        generate = gm.page.locator('#generateBtn')
        generate.wait_for(state='visible')
        # The hostile character name is shown as plain text and never runs
        assert XSS_NAME in gm.page.locator('#generationPanel').inner_text()
        assert generate.is_enabled(), gm.page.locator('#generationPanel').inner_text()
        generate.click()
        gm.page.wait_for_selector('text=Game generated:')
        gm.page.wait_for_selector('text=Regenerate')

        assert gm.page.evaluate('window.__xss') is None

        # --- A player's sheet: own data only -----------------------------------------------------------
        target = players[1]
        target.page.reload()
        target.page.click('text=View Character Sheet')
        target.page.wait_for_selector('#sheet:not([hidden])')
        target.page.wait_for_selector('#objectivesList .objective-row')
        char_id = target.page.url.split('char=')[1]
        sheet_text = target.page.locator('main').inner_text()
        assert target.page.locator('#secretsList .sheet-item').count() > 0
        assert target.page.locator('#relationshipsList .sheet-item').count() > 0
        assert target.page.locator('#objectivesList .objective-row').count() >= 4

        mine = target.api(f'/sessions/{session_id}/characters/mine')
        assert mine['id'] == char_id
        gm_secrets = gm.api(f'/sessions/{session_id}/secrets')['secrets']
        for secret in gm_secrets:
            knowers = {k['knower_character_id'] for k in secret['knowers']}
            if secret['subject_character_id'] != char_id:
                assert secret['text_subject'] not in sheet_text, 'another player\'s secret leaked onto this sheet'
                if char_id not in knowers and secret['text_knower']:
                    assert secret['text_knower'] not in sheet_text
        assert 'gm_hint' not in json.dumps(target.api(f'/sessions/{session_id}/objectives'))

        # Compose fills the form without saving
        target.page.click('#composeBtn')
        target.page.wait_for_function("document.getElementById('description').value.length > 20")
        suggested = target.page.input_value('#description')
        assert target.api(f'/sessions/{session_id}/characters/mine')['description'] != suggested

        # --- Lifepath is locked while generated ------------------------------------------------------------
        target.goto(f'build.html?session={session_id}&char={char_id}')
        target.page.wait_for_selector('.locked-banner')
        assert target.page.locator('#rolePicker').is_disabled()
        selects = target.page.locator('#builderContent select')
        assert selects.count() > 5
        for i in range(selects.count()):
            assert selects.nth(i).is_disabled()

        # --- Rename: the GM's master sheet shows the new name ---------------------------------------------------
        target.goto(f'character.html?session={session_id}&char={char_id}')
        target.page.wait_for_selector('#sheet:not([hidden])')
        target.page.fill('#displayName', RENAMED)
        target.page.click('#saveBtn')
        target.page.wait_for_selector('text=Saved successfully!')

        gm.goto(f'master.html?id={session_id}')
        gm.page.wait_for_selector('.character-panel')
        master_text = gm.page.locator('#masterContent').text_content()
        assert RENAMED in master_text
        relationship_texts = [r['description_source'] + r['description_target']
                              for r in gm.api(f'/sessions/{session_id}/relationships')['relationships']]
        assert any(RENAMED in t for t in relationship_texts), 'rename did not reach relationship text'
        assert gm.page.evaluate('window.__xss') is None

        # Killer is hidden until revealed
        assert gm.page.locator('.killer-badge').count() == 0
        assert 'Hidden' in master_text
        gm.page.click('#revealKillerBtn')
        gm.page.wait_for_selector('.killer-badge')
        assert gm.page.locator('.killer-badge').count() >= 1

        # --- GM resets: Lifepath becomes editable again -------------------------------------------------------
        gm.goto(f'lobby.html?id={session_id}')
        gm.page.click('text=Reset to clean slate')
        gm.page.click('dialog >> text=Reset game')
        gm.page.wait_for_selector('text=Game reset.')
        gm.page.wait_for_selector('#generateBtn')

        target.goto(f'build.html?session={session_id}&char={char_id}')
        target.page.wait_for_selector('#builderContent:not([hidden])')
        assert target.page.locator('.locked-banner').count() == 0
        assert target.page.locator('#rolePicker').is_enabled()
        assert target.page.locator('#builderContent select').first.is_enabled()

        # The XSS payload never executed anywhere, and no script errors were logged
        for p in everyone:
            assert p.page.evaluate('window.__xss') is None
        errors = [e for p in everyone for e in p.js_errors]
        assert not errors, errors
    finally:
        for p in everyone:
            p.context.close()
