import { requireUser } from '../auth.js';
import { apiFetch } from '../api.js';
import { el, clear } from '../dom.js';
import { getParam, renderError, renderLoading } from '../ui.js';
import { renderPrintSheet } from '../sheet-render.js';

const sessionId = getParam('id');
const sid = encodeURIComponent(sessionId || '');
const CONCURRENCY = 4;
const date = new Date().toLocaleDateString();

const $ = id => document.getElementById(id);

let characters = [];
const selected = new Set();
const sheets = new Map();   // character id -> { state: 'loading' | 'ok' | 'error', payload, message }

function refuse(message) {
    $('pickerPanel').hidden = true;
    clear($('printStatus')).appendChild(el('div', { class: 'panel', attrs: { role: 'alert' } },
        el('p', { text: message }),
        el('a', { text: 'Back to the lobby', attrs: { href: `lobby.html?id=${sid}` } })));
}

// --- Loading sheets, a few at a time ---------------------------------------------------------------

async function loadSheet(character) {
    sheets.set(character.id, { state: 'loading' });
    renderAll();
    try {
        const payload = await apiFetch(`/sessions/${sid}/characters/${encodeURIComponent(character.id)}/sheet`);
        sheets.set(character.id, { state: 'ok', payload });
    } catch (err) {
        sheets.set(character.id, { state: 'error', message: err.message });
    }
    renderAll();
}

async function loadAllSheets() {
    const queue = [...characters];
    async function worker() {
        while (queue.length) await loadSheet(queue.shift());
    }
    await Promise.all(Array.from({ length: Math.min(CONCURRENCY, queue.length) }, worker));
}

// --- Rendering ---------------------------------------------------------------------------------------

function renderPicker() {
    const list = clear($('characterPicker'));
    characters.forEach(c => {
        const id = `pick-${c.id}`;
        const box = el('input', { attrs: { type: 'checkbox', id, checked: selected.has(c.id) } });
        box.addEventListener('change', () => {
            if (box.checked) selected.add(c.id); else selected.delete(c.id);
            renderAll();
        });
        list.appendChild(el('li', {}, box,
            el('label', { text: `${c.display_name || 'Unnamed'} — ${c.role_key || 'no role'}`, attrs: { for: id } })));
    });
}

function renderBanner() {
    const holder = clear($('generationBanner'));
    const loaded = [...sheets.values()].find(s => s.state === 'ok');
    if (loaded && !loaded.payload.generated) {
        holder.appendChild(el('div', { class: 'locked-banner', attrs: { role: 'note' } },
            'Secrets, relationships and objectives are empty until you generate the game.'));
    }
}

function renderSheets() {
    const container = clear($('sheets'));
    characters.filter(c => selected.has(c.id)).forEach(c => {
        const entry = sheets.get(c.id) || { state: 'loading' };
        if (entry.state === 'ok') {
            container.appendChild(renderPrintSheet(entry.payload, date));
        } else if (entry.state === 'error') {
            // Screen-only: an error never prints
            container.appendChild(el('div', { class: 'panel no-print', attrs: { role: 'alert' } },
                el('p', { text: `Could not load ${c.display_name || 'this character'}: ${entry.message}` }),
                el('button', { class: 'secondary-btn inline-btn', text: 'Retry', attrs: { type: 'button' }, on: { click: () => loadSheet(c) } })));
        } else {
            container.appendChild(el('p', { class: 'state-message no-print', text: `Loading ${c.display_name || 'sheet'}...`, attrs: { role: 'status' } }));
        }
    });
}

function renderControls() {
    const chosen = characters.filter(c => selected.has(c.id));
    const waiting = chosen.filter(c => (sheets.get(c.id) || {}).state !== 'ok');
    let reason;
    if (chosen.length === 0) reason = 'Select at least one character to print.';
    else if (waiting.length) reason = 'Waiting for every selected sheet to load...';
    else reason = `${chosen.length} sheet${chosen.length === 1 ? '' : 's'} ready to print.`;
    $('printBtn').disabled = chosen.length === 0 || waiting.length > 0;
    $('printReason').textContent = reason;
}

function renderAll() {
    renderBanner();
    renderSheets();
    renderControls();
}

function setSelection(all) {
    selected.clear();
    if (all) characters.forEach(c => selected.add(c.id));
    renderPicker();
    renderAll();
}

$('selectAllBtn').addEventListener('click', () => setSelection(true));
$('selectNoneBtn').addEventListener('click', () => setSelection(false));
$('printBtn').addEventListener('click', () => window.print());

async function init() {
    if (!sessionId) {
        window.location.href = 'dashboard.html';
        return;
    }
    const user = await requireUser();
    if (!user) return;
    $('backLink').href = `master.html?id=${sid}`;

    const status = $('printStatus');
    renderLoading(status, 'Loading characters...');
    try {
        const session = await apiFetch(`/sessions/${sid}`);
        if (session.gm_id !== user.id) {
            refuse('Only the GM can print handouts for everyone. You can print your own sheet from your character page.');
            return;
        }
        const data = await apiFetch(`/sessions/${sid}/characters`);
        characters = [...data.characters].sort((a, b) => (a.display_name || '').localeCompare(b.display_name || ''));
        clear(status);
        if (characters.length === 0) {
            status.appendChild(el('p', { class: 'state-message', text: 'No characters yet. Players create them in the lobby.' }));
            return;
        }
        $('pickerPanel').hidden = false;
        characters.forEach(c => selected.add(c.id));
        renderPicker();
        renderAll();
        await loadAllSheets();
    } catch (err) {
        if (err.status === 403) {
            refuse('Only the GM can print handouts for everyone.');
        } else {
            renderError(status, `Could not load the characters: ${err.message}`, init);
        }
    }
}

init();
