import { requireUser } from '../auth.js';
import { apiFetch } from '../api.js';
import { el, clear, setText, showAlert, hideAlert } from '../dom.js';
import {
    badge, confirmDialog, copyButton, formatDateTime, getParam, labelize, renderEmpty, renderError,
    renderLoading, withBusy
} from '../ui.js';

const sessionId = getParam('id');
const sid = encodeURIComponent(sessionId || '');
const POLL_MS = 5000;
const MIN_CHARACTERS = 4; // the objective generator needs a killer, a scapegoat and two trail knowers

let currentUser = null;
let roles = [];
let busyAction = false;      // a Generate / Regenerate / Reset request is running
let pollFailures = 0;

const view = {
    session: null,
    members: [],
    characters: [],      // GM only
    readiness: null,     // GM only: lifepath-status
    master: null,        // GM only, once generated
    myChar: null,        // player only
};

// Re-render a container only when its content would change, so polling never wipes a form
// the player is typing in or collapses something they opened.
function renderIfChanged(container, signature, render) {
    const key = JSON.stringify(signature);
    if (container.dataset.signature === key) return;
    container.dataset.signature = key;
    clear(container);
    render(container);
}

async function tryGet(path) {
    try {
        return await apiFetch(path);
    } catch (err) {
        if (err.status === 404) return null;
        throw err;
    }
}

async function fetchRoles() {
    try {
        const res = await fetch('data/roles.json');
        roles = await res.json();
    } catch (e) {
        showAlert('Could not load the list of roles. Reload the page to try again.');
    }
}

// --- Loading ----------------------------------------------------------------------------------

async function loadData() {
    const session = await apiFetch(`/sessions/${sid}`);
    const membersData = await apiFetch(`/sessions/${sid}/members`);
    view.session = session;
    view.members = membersData.members;

    const isGm = session.gm_id === currentUser.id;
    if (isGm) {
        const [chars, readiness] = await Promise.all([
            apiFetch(`/sessions/${sid}/characters`),
            apiFetch(`/sessions/${sid}/lifepath-status`)
        ]);
        view.characters = chars.characters;
        view.readiness = readiness;
        view.master = session.status === 'generated' ? await apiFetch(`/sessions/${sid}/master`) : null;
    } else {
        view.myChar = await tryGet(`/sessions/${sid}/characters/mine`);
    }
}

// --- Rendering --------------------------------------------------------------------------------

function renderHeader() {
    const { session } = view;
    document.title = `${session.name} - Lobby`;
    setText('sessionNameTitle', session.name);
    setText('joinCodeDisplay', session.join_code);
    renderIfChanged(document.getElementById('joinCodeActions'), session.join_code, container => {
        container.appendChild(copyButton(session.join_code, 'Copy join code'));
    });
}

function renderMembers() {
    const { session, members, characters } = view;
    const isGm = session.gm_id === currentUser.id;
    const container = document.getElementById('membersList');
    const rows = members.map(m => ({
        id: m.user_id,
        name: m.display_name,
        gm: m.user_id === session.gm_id,
        role: isGm ? (characters.find(c => c.player_id === m.user_id)?.role_key || null) : undefined
    }));
    renderIfChanged(container, rows, () => {
        if (rows.length === 0) {
            renderEmpty(container, 'Nobody has joined yet.', 'Share the join code above with your players.');
            return;
        }
        const list = el('ul', { class: 'plain-list' });
        rows.forEach(r => {
            list.appendChild(el('li', { class: 'row-item' },
                el('span', { text: r.gm ? `${r.name} (GM)` : r.name }),
                isGm && !r.gm
                    ? (r.role ? el('span', { class: 'role-tag', text: r.role }) : el('span', { class: 'muted', text: 'Waiting to pick...' }))
                    : null));
        });
        container.appendChild(list);
    });
}

function createCharacterForm() {
    const nameInput = el('input', { attrs: { type: 'text', id: 'myCharName', maxlength: '50', autocomplete: 'off', required: true } });
    const roleSelect = el('select', { attrs: { id: 'myRoleSelect', required: true } },
        el('option', { text: 'Select your role...', attrs: { value: '' } }),
        roles.map(r => el('option', { text: r.label, attrs: { value: r.key } })));
    const submit = el('button', { text: 'Create Character', attrs: { type: 'submit', disabled: true } });
    const check = () => { submit.disabled = !(nameInput.value.trim() && roleSelect.value); };
    nameInput.addEventListener('input', check);
    roleSelect.addEventListener('change', check);

    const form = el('form', { attrs: { id: 'createCharacterForm' } },
        el('label', { text: 'Character name', attrs: { for: 'myCharName' } }), nameInput,
        el('label', { text: 'Role', attrs: { for: 'myRoleSelect' } }), roleSelect,
        submit);
    form.addEventListener('submit', async e => {
        e.preventDefault();
        hideAlert();
        await withBusy(submit, 'Creating...', async () => {
            try {
                await apiFetch(`/sessions/${sid}/characters`, {
                    method: 'POST',
                    body: JSON.stringify({ role_key: roleSelect.value, display_name: nameInput.value.trim() })
                });
                const created = await apiFetch(`/sessions/${sid}/characters/mine`);
                window.location.href = `build.html?session=${sid}&char=${encodeURIComponent(created.id)}`;
            } catch (err) {
                showAlert(err.message);   // the form keeps what was typed
            }
        });
    });
    return form;
}

function renderPlayerPanel() {
    const { session, myChar } = view;
    const isGm = session.gm_id === currentUser.id;
    document.getElementById('playerPanel').hidden = isGm;
    if (isGm) return;

    const container = document.getElementById('playerStatus');
    const locked = session.status !== 'lobby';
    const signature = { char: myChar ? [myChar.id, myChar.role_key, myChar.display_name, myChar.lifepath_complete] : null, locked };

    // The creation form is built once and left alone while the player types in it
    if (!myChar && !locked && container.querySelector('#createCharacterForm')) return;

    renderIfChanged(container, signature, () => {
        if (myChar) {
            container.appendChild(el('p', {}, 'Your character: ', el('strong', { text: myChar.display_name || 'Unnamed' }),
                ' — role ', el('strong', { class: 'role-tag', text: myChar.role_key || 'none' })));
            container.appendChild(el('p', { class: myChar.lifepath_complete ? '' : 'muted' },
                myChar.lifepath_complete ? 'Your Lifepath is complete.' : 'Your Lifepath is not finished yet.'));
            if (locked) {
                container.appendChild(el('p', { class: 'muted', text: 'The game has been generated. Open your sheet to see your secrets, relationships and objectives.' }));
            }
            container.appendChild(el('div', { class: 'button-row' },
                el('a', { class: 'button-link', text: 'View Character Sheet',
                    attrs: { href: `character.html?session=${sid}&char=${encodeURIComponent(myChar.id)}` } }),
                el('a', { class: 'button-link', text: locked ? 'View Lifepath (locked)' : 'Lifepath Choices',
                    attrs: { href: `build.html?session=${sid}&char=${encodeURIComponent(myChar.id)}` } })));
        } else if (locked) {
            container.appendChild(el('p', { class: 'muted', text: 'The game has already been generated, so no new characters can be created.' }));
        } else {
            container.appendChild(el('p', { class: 'muted', text: 'Pick a name and a role to create your character.' }));
            container.appendChild(createCharacterForm());
        }
    });
}

// --- GM generation panel ----------------------------------------------------------------------

function readinessRows() {
    const { readiness, characters } = view;
    return readiness.members.map(m => {
        const char = characters.find(c => c.id === m.character_id);
        return {
            player: m.player_display_name,
            character: char?.display_name || null,
            role: m.role_key,
            ready: m.lifepath_complete,
            hasCharacter: !!m.character_id
        };
    });
}

function notReadyReason(rows) {
    const reasons = [];
    const withCharacter = rows.filter(r => r.hasCharacter).length;
    rows.filter(r => !r.hasCharacter).forEach(r => reasons.push(`${r.player} has no character yet`));
    rows.filter(r => r.hasCharacter && !r.ready).forEach(r => reasons.push(`${r.character || r.player} has not finished their Lifepath`));
    if (withCharacter < MIN_CHARACTERS) {
        reasons.push(`at least ${MIN_CHARACTERS} characters are needed (there ${withCharacter === 1 ? 'is' : 'are'} ${withCharacter})`);
    }
    return reasons;
}

async function runPipeline(button, { url, busyLabel, success }) {
    busyAction = true;
    hideAlert();
    let succeeded = false;
    await withBusy(button, busyLabel, async () => {
        try {
            const result = await apiFetch(url, { method: 'POST' });
            showAlert(success(result), 'success');
            succeeded = true;
        } catch (err) {
            showAlert(err.message);
        }
    });
    busyAction = false;
    if (succeeded) await refresh({ force: true });
}

function readinessPanel(container) {
    const rows = readinessRows();
    const reasons = notReadyReason(rows);
    const canGenerate = reasons.length === 0;

    container.appendChild(el('p', { class: 'muted',
        text: `Generation builds relationships, secrets and objectives in one step. It needs at least ${MIN_CHARACTERS} characters, each with a finished Lifepath.` }));

    if (rows.length === 0) {
        container.appendChild(el('p', { class: 'muted', text: 'No players have joined yet.' }));
    } else {
        const list = el('ul', { class: 'plain-list', attrs: { 'aria-label': 'Lifepath readiness' } });
        rows.forEach(r => {
            list.appendChild(el('li', { class: 'row-item' },
                el('span', {}, el('strong', { text: r.character || r.player }),
                    r.character ? el('span', { class: 'muted', text: ` (${r.player})` }) : null,
                    ' ', el('span', { class: 'role-tag', text: r.role || 'no role' })),
                r.ready ? badge('Ready', 'ok') : badge(r.hasCharacter ? 'Lifepath incomplete' : 'No character', 'danger')));
        });
        container.appendChild(list);
    }

    const reasonId = 'generateReason';
    const generate = el('button', { text: 'Generate Game', attrs: { id: 'generateBtn', type: 'button', disabled: !canGenerate, 'aria-describedby': reasonId } });
    generate.addEventListener('click', () => runPipeline(generate, {
        url: `/sessions/${sid}/generate`,
        busyLabel: 'Generating...',
        success: r => `Game generated: ${r.relationships} relationships, ${r.secrets} secrets, ${r.objectives} objectives.`
    }));
    container.appendChild(el('p', { class: canGenerate ? 'muted' : 'field-error', attrs: { id: reasonId },
        text: canGenerate ? 'Everyone is ready.' : `Cannot generate yet: ${reasons.join('; ')}.` }));
    container.appendChild(el('div', { class: 'button-row' }, generate, masterLink('Open master sheet')));
}

function masterLink(label) {
    return el('a', { class: 'button-link', text: label, attrs: { href: `master.html?id=${sid}` } });
}

function generatedPanel(container) {
    const { session, master } = view;
    const counts = {
        relationships: master.relationships.length,
        secrets: master.secrets.length,
        objectives: master.objectives.length
    };

    container.appendChild(el('p', {}, badge('Generated', 'ok'),
        ' ', el('span', { class: 'muted', text: session.generated_at ? `on ${formatDateTime(session.generated_at)}` : '' })));
    container.appendChild(el('div', { class: 'stat-row' },
        Object.entries(counts).map(([label, n]) => el('div', {}, el('strong', { text: n }), el('span', { class: 'muted', text: label })))));
    if (session.generation_seed !== null && session.generation_seed !== undefined) {
        container.appendChild(el('p', { class: 'seed' }, 'Seed ', el('code', { text: session.generation_seed }),
            copyButton(session.generation_seed, 'Copy seed')));
    }
    container.appendChild(el('p', { class: 'muted',
        text: 'Role, Lifepath and the killer are locked while the game is generated. Reset to a clean slate to change them.' }));

    const regenerate = el('button', { class: 'secondary-btn danger-btn', text: 'Regenerate', attrs: { type: 'button' } });
    regenerate.addEventListener('click', async () => {
        const ok = await confirmDialog(
            'Regenerating replaces all relationships, secrets and objectives, including any edits you or the players made to objectives. The killer stays the same.',
            { title: 'Regenerate the game?', confirmLabel: 'Regenerate', danger: true });
        if (!ok) return;
        await runPipeline(regenerate, {
            url: `/sessions/${sid}/generate?regenerate=true`,
            busyLabel: 'Regenerating...',
            success: r => `Game regenerated: ${r.relationships} relationships, ${r.secrets} secrets, ${r.objectives} objectives.`
        });
    });

    const reset = el('button', { class: 'secondary-btn danger-btn', text: 'Reset to clean slate', attrs: { type: 'button' } });
    reset.addEventListener('click', async () => {
        const ok = await confirmDialog(
            'Resetting deletes all generated content (relationships, secrets, objectives and every edit to them) but keeps the characters. It unlocks role, Lifepath and killer edits. Killer selections are kept.',
            { title: 'Reset to a clean slate?', confirmLabel: 'Reset game', danger: true });
        if (!ok) return;
        await runPipeline(reset, {
            url: `/sessions/${sid}/reset`,
            busyLabel: 'Resetting...',
            success: r => `Game reset. Removed ${r.deleted.relationships} relationships, ${r.deleted.secrets} secrets and ${r.deleted.objectives} objectives.`
        });
    });

    container.appendChild(el('div', { class: 'button-row' }, masterLink('Open master sheet'), regenerate, reset));
}

function renderGmPanel() {
    const { session } = view;
    const isGm = session.gm_id === currentUser.id;
    document.getElementById('gmPanel').hidden = !isGm;
    if (!isGm || busyAction) return;   // never rebuild buttons under a running request

    const container = document.getElementById('generationPanel');
    const signature = {
        status: session.status, seed: session.generation_seed, at: session.generated_at,
        rows: view.readiness ? readinessRows() : null,
        counts: view.master ? [view.master.relationships.length, view.master.secrets.length, view.master.objectives.length] : null
    };
    renderIfChanged(container, signature, () => {
        if (session.status === 'generated' && view.master) {
            generatedPanel(container);
        } else if (session.status === 'lobby') {
            readinessPanel(container);
        } else {
            container.appendChild(el('p', {}, badge(labelize(session.status), 'neutral'),
                ' The game is ', el('strong', { text: session.status }), '; generation controls are not available.'));
            container.appendChild(el('div', { class: 'button-row' }, masterLink('Open master sheet')));
        }
    });
}

function renderAll() {
    renderHeader();
    renderPlayerPanel();
    renderMembers();
    renderGmPanel();
}

// --- Refresh loop -----------------------------------------------------------------------------

async function refresh({ force = false } = {}) {
    if (busyAction && !force) return;
    try {
        await loadData();
        pollFailures = 0;
        renderAll();
    } catch (err) {
        pollFailures += 1;
        if (!view.session) {
            // First load failed: show an error with a retry instead of a blank page
            renderError(document.getElementById('membersList'), err.message, () => refresh({ force: true }));
        } else if (pollFailures === 1) {
            showAlert(`Could not refresh: ${err.message}`);
        }
    }
}

async function init() {
    if (!sessionId) {
        window.location.href = 'dashboard.html';
        return;
    }
    currentUser = await requireUser();
    if (!currentUser) return;

    renderLoading(document.getElementById('membersList'), 'Loading session...');
    await fetchRoles();
    await refresh({ force: true });

    setInterval(() => { if (!document.hidden) refresh(); }, POLL_MS);
}

init();
