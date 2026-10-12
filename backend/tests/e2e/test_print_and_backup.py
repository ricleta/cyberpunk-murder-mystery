"""Phase 7A in the browser: printing, export and restore.

Needs Playwright + Chromium (see test_smoke.py). One generated game is shared by the tests
in this module; tests that change its state (reset) are last and say so.
"""
import json

import pytest

from support import Player, XSS_NAME, browser, create_character, finish_lifepath, servers  # noqa: F401 (fixtures)

pytestmark = pytest.mark.e2e

pytest.importorskip(
    'playwright.sync_api',
    reason='Playwright is not installed: pip install -r requirements-dev.txt && playwright install chromium')

ROLES = ['solo', 'netrunner', 'tech', 'exec']


@pytest.fixture(scope='module')
def game(servers, browser):
    gm = Player(browser, servers, 'gm_7a')
    players = [Player(browser, servers, f'p7a_{i}') for i in range(4)]
    extra = []
    try:
        gm.signup()
        gm.page.fill('#sessionName', 'Print Session')
        gm.page.click('#createSubmit')
        gm.page.wait_for_url('**/lobby.html?id=*')
        session_id = gm.page.url.split('id=')[1]
        gm.page.wait_for_function("document.getElementById('joinCodeDisplay').textContent !== '---'")
        join_code = gm.page.locator('#joinCodeDisplay').inner_text()

        for i, player in enumerate(players):
            player.signup()
            player.join(join_code)
            create_character(player, f'Hero {i}', ROLES[i])
            finish_lifepath(player)

        gm.page.reload()
        gm.page.locator('#generateBtn').wait_for(state='visible')
        gm.page.click('#generateBtn')
        gm.page.wait_for_selector('text=Game generated:')

        chars = gm.api(f'/sessions/{session_id}/characters')['characters']
        by_name = {c['display_name']: c['id'] for c in chars}
        yield {'gm': gm, 'players': players, 'session_id': session_id, 'join_code': join_code,
               'chars': by_name, 'extra': extra, 'servers': servers, 'browser': browser}
    finally:
        for p in [gm, *players, *extra]:
            p.context.close()


def all_errors(game):
    return [e for p in [game['gm'], *game['players'], *game['extra']] for e in p.js_errors]


def sheet_url(game, name):
    return f"character.html?session={game['session_id']}&char={game['chars'][name]}"


def open_sheet(player, game, name):
    player.goto(sheet_url(game, name))
    player.page.wait_for_selector('#sheet:not([hidden])')
    player.page.wait_for_selector('#objectivesList .objective-row')


def test_player_sheet_prints_cleanly(game):
    player = game['players'][1]
    open_sheet(player, game, 'Hero 1')
    page = player.page
    page.fill('#description', 'Line one\nLine two')
    page.fill('#traits', 'calm, sharp')
    page.emulate_media(media='print')

    assert page.locator('.print-header').is_visible()
    assert page.locator('.print-footer').is_visible()
    assert 'hero 1' in page.locator('.print-header').inner_text().lower()
    assert 'print session' in page.locator('.print-header').inner_text().lower()
    assert 'Private' in page.locator('.print-footer').inner_text()
    for hidden in ('#description', '#traits', '#displayName', '#saveBtn', '#printBtn', '#composeBtn', '#addObjectiveForm'):
        assert not page.locator(hidden).is_visible(), hidden
    assert 'Line two' in page.locator('#printDescription').inner_text()
    assert 'calm, sharp' in page.locator('#printTraits').inner_text()

    text = page.locator('main').inner_text()
    mine = player.api(f"/sessions/{game['session_id']}/characters/mine")
    assert any(s['text'] in text for s in mine['secrets'])
    assert all(o['text'] in text for o in mine['objectives'])
    for section in ('Description', 'Traits', 'Secrets & Lies', 'Relationships', 'Objectives'):
        assert section.lower() in text.lower()
    page.emulate_media(media='screen')


def test_gm_hints_never_print(game):
    gm = game['gm']
    open_sheet(gm, game, 'Hero 2')
    page = gm.page
    objectives = gm.api(f"/sessions/{game['session_id']}/objectives?character_id={game['chars']['Hero 2']}")['objectives']
    hints = [o['gm_hint'] for o in objectives if o.get('gm_hint')]
    assert hints, 'the GM should receive hints for this character'

    assert page.locator('.gm-only').count() >= len(hints)
    screen_text = page.locator('main').inner_text()
    assert any(h in page.locator('main').text_content() for h in hints)

    page.emulate_media(media='print')
    for i in range(page.locator('.gm-only').count()):
        assert not page.locator('.gm-only').nth(i).is_visible()
    printed = page.locator('main').inner_text()
    for hint in hints:
        assert hint not in printed
    assert 'GM hint' not in printed
    page.emulate_media(media='screen')
    assert screen_text


def test_gm_print_all_sheets_page(game):
    gm = game['gm']
    sheet_bodies = []
    gm.page.on('response', lambda r: sheet_bodies.append(r) if r.url.endswith('/sheet') else None)
    gm.goto(f"print.html?id={game['session_id']}")
    gm.page.wait_for_selector('.print-sheet')
    gm.page.wait_for_function('document.querySelectorAll(".print-sheet").length === 4')

    # Unselect two characters: only two sheets remain, one per character
    gm.page.uncheck('label:has-text("Hero 2") >> xpath=preceding-sibling::input')
    gm.page.uncheck('label:has-text("Hero 3") >> xpath=preceding-sibling::input')
    assert gm.page.locator('.print-sheet').count() == 2
    assert gm.page.locator('#printBtn').is_enabled()

    gm.page.emulate_media(media='print')
    assert gm.page.locator('.print-sheet').count() == 2
    for i in range(2):
        assert gm.page.locator('.print-sheet').nth(i).is_visible()
    assert not gm.page.locator('#pickerPanel').is_visible()
    text = gm.page.locator('main').inner_text()
    assert gm.page.locator('.print-sheet-title').all_text_contents() == ['Hero 0', 'Hero 1']
    assert text.count('Do not share your sheet') == 2

    objectives = gm.api(f"/sessions/{game['session_id']}/objectives")['objectives']
    for o in objectives:
        if o.get('gm_hint'):
            assert o['gm_hint'] not in text
    assert gm.page.locator('.killer-badge').count() == 0
    gm.page.emulate_media(media='screen')

    for response in sheet_bodies:
        body = response.text()
        assert 'is_killer' not in body and 'gm_hint' not in body and 'template_id' not in body


def test_non_gm_cannot_use_print_all(game):
    player = game['players'][0]
    player.goto(f"print.html?id={game['session_id']}")
    player.page.wait_for_selector('text=Only the GM can print')
    assert player.page.locator('.print-sheet').count() == 0
    assert not player.page.locator('#pickerPanel').is_visible()


def test_export_downloads_a_backup_file(game, tmp_path):
    player = game['players'][2]
    open_sheet(player, game, 'Hero 2')
    with player.page.expect_download() as info:
        player.page.click('#exportBtn')
    download = info.value
    assert download.suggested_filename == 'cyberpunk-character-hero-2.json'
    data = json.loads(open(download.path(), encoding='utf-8').read())
    assert data['format'] == 'cyberpunk-murder-mystery/character' and data['schema_version'] == 1
    assert data['character']['display_name'] == 'Hero 2' and data['character']['role']['id'] == 'tech'
    for forbidden in ('is_killer', 'gm_hint', 'secrets', 'objectives', 'relationships'):
        assert forbidden not in json.dumps(data)

    # the GM can download a bundle from the master sheet
    gm = game['gm']
    gm.goto(f"master.html?id={game['session_id']}")
    gm.page.wait_for_selector('.character-panel')
    with gm.page.expect_download() as info:
        gm.page.click('text=Export all characters')
    bundle = json.loads(open(info.value.path(), encoding='utf-8').read())
    assert bundle['format'].endswith('character-bundle') and len(bundle['characters']) == 4
    assert info.value.suggested_filename == 'cyberpunk-characters-print-session.json'

    (tmp_path / 'hero2.json').write_text(json.dumps(data), encoding='utf-8')
    (tmp_path / 'bundle.json').write_text(json.dumps(bundle), encoding='utf-8')
    game['export_dir'] = tmp_path


def choose_file(page, path):
    with page.expect_file_chooser() as chooser:
        page.click('#importChooseBtn')
    chooser.value.set_files(str(path))


def test_reset_then_restore_from_file_and_rename(game):
    """Changes the game's state: resets it, then restores a character in the lobby."""
    gm, servers, browser = game['gm'], game['servers'], game['browser']
    gm.goto(f"lobby.html?id={game['session_id']}")
    gm.page.click('text=Reset to clean slate')
    gm.page.click('dialog >> text=Reset game')
    gm.page.wait_for_selector('text=Game reset.')

    restorer = Player(browser, servers, 'restorer_7a')
    game['extra'].append(restorer)
    restorer.signup()
    restorer.join(game['join_code'])
    restorer.page.click('#restoreBtn')
    choose_file(restorer.page, game['export_dir'] / 'bundle.json')

    # a bundle asks which character to restore
    restorer.page.wait_for_selector('#bundlePick')
    restorer.page.select_option('#bundlePick', label='Hero 2 — Tech')
    restorer.page.click('text=Use this character')

    restorer.page.wait_for_selector('.import-report')
    report_text = restorer.page.locator('.import-report').inner_text()
    assert 'will change' in report_text.lower() and 'Hero 2' in report_text
    restorer.page.uncheck('#importIncludeText')
    restorer.page.wait_for_function("!document.getElementById('importIncludeText').checked && !document.querySelector('.import-report').innerText.includes('Description')")
    restorer.page.check('#importIncludeText')
    restorer.page.wait_for_function("document.querySelector('.import-report').innerText.includes('Description')")
    restorer.page.click('#importApplyBtn')
    restorer.page.wait_for_url('**/build.html?*')

    mine = restorer.api(f"/sessions/{game['session_id']}/characters/mine")
    original = json.loads((game['export_dir'] / 'hero2.json').read_text(encoding='utf-8'))['character']
    assert mine['display_name'] == 'Hero 2' and mine['role_key'] == 'tech'
    assert mine['personality'] == original['lifepath']['shared']['personality']['id']
    assert mine['description'] == original['description']
    assert mine['lifepath_complete'] is True
    restorer.page.wait_for_selector('#builderContent:not([hidden])')
    assert 'READY' in restorer.page.locator('#completionTracker').inner_text()

    # rename it, then export again
    restorer.goto(f"character.html?session={game['session_id']}&char={mine['id']}")
    restorer.page.wait_for_selector('#sheet:not([hidden])')
    restorer.page.fill('#displayName', 'Restored Hero')
    restorer.page.click('#saveBtn')
    restorer.page.wait_for_selector('text=Saved successfully!')
    with restorer.page.expect_download() as info:
        restorer.page.click('#exportBtn')
    assert info.value.suggested_filename == 'cyberpunk-character-restored-hero.json'
    assert json.loads(open(info.value.path(), encoding='utf-8').read())['character']['display_name'] == 'Restored Hero'


def test_hostile_file_is_shown_as_skipped_and_never_runs(game, tmp_path):
    """Runs after the reset above, so the session is in the lobby again."""
    servers, browser = game['servers'], game['browser']
    original = json.loads((game['export_dir'] / 'hero2.json').read_text(encoding='utf-8'))
    original['character']['display_name'] = XSS_NAME
    original['character']['lifepath']['shared']['personality'] = {'id': 'not_an_option', 'text': 'Not an option'}
    original['character']['lifepath']['shared']['hairstyle'] = {'id': '<script>window.__xss=1</script>', 'text': ''}
    original['character']['is_killer'] = True
    path = tmp_path / 'hostile.json'
    path.write_text(json.dumps(original), encoding='utf-8')

    newcomer = Player(browser, servers, 'hostile_7a')
    game['extra'].append(newcomer)
    newcomer.signup()
    newcomer.join(game['join_code'])
    newcomer.page.click('#restoreBtn')
    choose_file(newcomer.page, path)
    newcomer.page.wait_for_selector('.import-report')
    report = newcomer.page.locator('.import-report').inner_text()
    assert 'skipped' in report.lower()
    assert newcomer.page.locator('.import-warnings').inner_text().count('is_killer') == 1
    assert newcomer.page.evaluate('window.__xss') is None
    newcomer.page.click('#importApplyBtn')
    newcomer.page.wait_for_url('**/build.html?*')

    mine = newcomer.api(f"/sessions/{game['session_id']}/characters/mine")
    assert mine['display_name'] == XSS_NAME           # stored as plain text
    assert mine['is_killer'] is False and mine['personality'] is None
    newcomer.goto(f"character.html?session={game['session_id']}&char={mine['id']}")
    newcomer.page.wait_for_selector('#sheet:not([hidden])')
    assert newcomer.page.evaluate('window.__xss') is None
    game['gm'].goto(f"lobby.html?id={game['session_id']}")
    game['gm'].page.wait_for_selector('#generationPanel')
    game['gm'].page.wait_for_timeout(500)
    assert game['gm'].page.evaluate('window.__xss') is None

    assert not all_errors(game), all_errors(game)
