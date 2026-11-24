"""Browser test: the GM kicks a player after generation and is prompted to regenerate."""
import pytest

from support import Player, finish_lifepath, create_character, browser, servers  # noqa: F401 (fixtures)

pytestmark = pytest.mark.e2e

pytest.importorskip(
    "playwright.sync_api",
    reason="Playwright is not installed: pip install -r requirements-dev.txt && playwright install chromium")


def confirm(page, label):
    page.locator('dialog.confirm-dialog').wait_for(state='visible')
    page.locator('dialog.confirm-dialog button', has_text=label).click()
    page.locator('dialog.confirm-dialog').wait_for(state='detached')


def test_gm_kicks_a_player_after_generation(servers, browser):
    gm = Player(browser, servers, 'kick_gm')
    players = [Player(browser, servers, f'kick_player_{i}') for i in range(5)]
    everyone = [gm] + players
    try:
        gm.signup()
        gm.page.fill('#sessionName', 'Kick Session')
        gm.page.click('#createSubmit')
        gm.page.wait_for_url('**/lobby.html?id=*')
        gm.page.wait_for_function("document.getElementById('joinCodeDisplay').textContent !== '---'")
        join_code = gm.page.locator('#joinCodeDisplay').inner_text()

        roles = ['solo', 'netrunner', 'tech', 'exec', 'fixer']
        for i, player in enumerate(players):
            player.signup()
            player.join(join_code)
            create_character(player, f'Kick Hero {i}', roles[i])
            finish_lifepath(player)

        gm.page.reload()
        gm.page.locator('#generateBtn').click()
        gm.page.wait_for_selector('text=Game generated:')

        leaver = players[4]
        leaver.goto(gm.page.url.split('/')[-1])   # the kicked player sits in the lobby
        leaver.page.wait_for_selector('#playerStatus >> text=View Character Sheet')

        # --- Kick: warned that the game is cleared, then offered a regeneration -------------------
        gm.page.locator('button[aria-label="Remove kick_player_4 from the session"]').click()
        dialog_text = gm.page.locator('dialog.confirm-dialog').inner_text()
        assert 'deletes ALL relationships, secrets and objectives' in dialog_text
        confirm(gm.page, 'Remove and clear game')
        gm.page.locator('dialog.confirm-dialog', has_text='Regenerate now?').wait_for()
        confirm(gm.page, 'Regenerate now')
        gm.page.wait_for_selector('text=kick_player_4 was removed and the game was regenerated')
        gm.page.wait_for_selector('text=Regenerate')
        assert 'kick_player_4' not in gm.page.locator('#membersList').inner_text()

        # --- The kicked player is sent back to the dashboard ---------------------------------------
        leaver.page.wait_for_url('**/dashboard.html*', timeout=15000)
        leaver.page.wait_for_selector('text=The GM removed you from that session.')
        assert 'Kick Session' not in leaver.page.locator('#mySessions').inner_text()

        for p in everyone:
            assert not p.js_errors, p.js_errors
    finally:
        for p in everyone:
            p.context.close()
