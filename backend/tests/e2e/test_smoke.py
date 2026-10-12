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

import pytest

from support import Player, finish_lifepath, XSS_NAME, browser, servers  # noqa: F401 (fixtures)

pytestmark = pytest.mark.e2e

pytest.importorskip(
    "playwright.sync_api",
    reason="Playwright is not installed: pip install -r requirements-dev.txt && playwright install chromium")

RENAMED = 'Renamed Hero'


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
