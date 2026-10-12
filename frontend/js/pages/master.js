import { requireUser } from '../auth.js';
import { apiFetch } from '../api.js';
import { el, clear, showAlert, hideAlert } from '../dom.js';
import { downloadJson, slugify } from '../files.js';
import {
    badge, copyButton, formatDateTime, getParam, labelize, loadInto, pointsBadge, pointsLabel, renderEmpty
} from '../ui.js';

const sessionId = getParam('id');
const sid = encodeURIComponent(sessionId || '');

const KIND_LABELS = { victim_tie: 'Tie to the victim', self: 'Secret', lie: 'Lie', observation: 'Observation' };
const KILLER_ONLY_OBJECTIVES = new Set(['hide_evidence', 'frame']);
const HIDDEN_TEXT = 'Hidden — reveal killer to show';

// SPOILER GUARD, NOT A SECURITY BOUNDARY: this only stops a GM who also plays from seeing
// the killer by accident while the sheet is open. The data is still in the API response
// (the GM is allowed to see it); nothing here, and nothing persisted, hides it from the
// network tab. The reveal state lives in memory only and resets on reload.
let killerRevealed = false;

let data = null;
const openPanels = new Set();       // character ids whose panel is open
let openCell = null;                // {knower, subject} of the expanded matrix cell
let printOpened = [];

const content = document.getElementById('masterContent');

function charName(id) {
    return data.characters.find(c => c.id === id)?.display_name || 'Unknown';
}

function isKillerSecret(s) {
    return s.is_killer_core || s.is_killer_trail;
}

function redactedRow() {
    return el('div', { class: 'redacted', text: HIDDEN_TEXT });
}

function secretBadges(s) {
    const items = [badge(KIND_LABELS[s.kind] || labelize(s.kind), s.kind === 'lie' ? 'warn' : 'info'),
        badge(labelize(s.category), 'neutral'),
        badge(`Severity ${s.severity}`, s.severity >= 3 ? 'danger' : 'neutral')];
    if (killerRevealed && s.is_killer_core) items.push(badge('Killer core', 'danger'));
    if (killerRevealed && s.is_killer_trail) items.push(badge('Killer trail', 'danger'));
    return el('div', { class: 'item-badges' }, items);
}

// --- Header ----------------------------------------------------------------------------------------

function renderHeader() {
    const { session } = data;
    document.title = `${session.name} - Master Sheet`;
    const counts = [
        ['characters', data.characters.length], ['relationships', data.relationships.length],
        ['secrets', data.secrets.length], ['objectives', data.objectives.length]
    ];
    const reveal = el('button', { class: 'secondary-btn inline-btn', text: killerRevealed ? 'Hide killer' : 'Reveal killer',
        attrs: { type: 'button', id: 'revealKillerBtn', 'aria-pressed': String(killerRevealed) } });
    reveal.addEventListener('click', () => { killerRevealed = !killerRevealed; renderAll(); document.getElementById('revealKillerBtn').focus(); });

    return el('section', { class: 'panel', attrs: { 'aria-labelledby': 'summaryHeading' } },
        el('h2', { text: session.name, attrs: { id: 'summaryHeading' } }),
        el('p', {}, badge(labelize(session.status), session.status === 'generated' ? 'ok' : 'info'), ' ',
            session.generated_at ? el('span', { class: 'muted', text: `Generated ${formatDateTime(session.generated_at)}` })
                : el('span', { class: 'muted', text: 'Nothing generated yet.' })),
        session.generation_seed !== null && session.generation_seed !== undefined
            ? el('p', { class: 'seed' }, 'Seed ', el('code', { text: session.generation_seed }), copyButton(session.generation_seed, 'Copy seed'))
            : null,
        el('div', { class: 'stat-row' }, counts.map(([label, n]) => el('div', {}, el('strong', { text: n }), el('span', { class: 'muted', text: label })))),
        el('div', { class: 'toolbar' }, reveal,
            el('a', { class: 'button-link', text: 'Manage generation in the lobby', attrs: { href: `lobby.html?id=${sid}` } }),
            el('button', { class: 'secondary-btn inline-btn no-print', text: 'Export all characters', attrs: { type: 'button' }, on: { click: exportAll } }),
            el('a', { class: 'button-link no-print', text: 'Print all sheets', attrs: { href: `print.html?id=${sid}` } }),
            el('button', { class: 'secondary-btn inline-btn no-print', text: 'Refresh', attrs: { type: 'button' }, on: { click: reload } }),
            el('button', { class: 'secondary-btn inline-btn no-print', text: 'Print', attrs: { type: 'button' }, on: { click: () => window.print() } })),
        el('p', { class: 'muted', text: killerRevealed ? 'The killer is visible on this page.' : 'The killer is hidden on this page until you reveal it.' }));
}

async function exportAll(event) {
    hideAlert();
    const button = event.currentTarget;
    button.disabled = true;
    try {
        const bundle = await apiFetch(`/sessions/${sid}/characters/export`);
        downloadJson(`cyberpunk-characters-${slugify(data.session.name)}.json`, bundle);
    } catch (err) {
        showAlert(err.message);
    } finally {
        button.disabled = false;
    }
}

// --- Characters table -----------------------------------------------------------------------------

async function toggleKiller(character, checked) {
    hideAlert();
    try {
        if (checked) {
            // Only one killer can exist: clear the previous one first
            for (const other of data.characters.filter(c => c.is_killer && c.id !== character.id)) {
                await apiFetch(`/sessions/${sid}/characters/${encodeURIComponent(other.id)}`, { method: 'PATCH', body: JSON.stringify({ is_killer: false }) });
            }
        }
        await apiFetch(`/sessions/${sid}/characters/${encodeURIComponent(character.id)}`, { method: 'PATCH', body: JSON.stringify({ is_killer: checked }) });
    } catch (err) {
        showAlert(err.message);
    }
    await reload();
}

function renderCharactersTable() {
    const lobby = data.session.status === 'lobby';
    const section = el('section', { class: 'panel', attrs: { 'aria-labelledby': 'charactersHeading' } },
        el('h2', { text: 'Characters', attrs: { id: 'charactersHeading' } }));
    if (data.characters.length === 0) {
        renderEmpty(section.appendChild(el('div')), 'No characters created yet.', 'Characters appear here as players build them in the lobby.');
        return section;
    }
    const body = el('tbody');
    data.characters.forEach(c => {
        const killerCell = killerRevealed
            ? el('td', {}, el('input', {
                attrs: { type: 'checkbox', 'aria-label': `${c.display_name} is the killer`, checked: c.is_killer, disabled: !lobby,
                    title: lobby ? null : 'Locked: reset the game in the lobby to change the killer' },
                on: { change: e => toggleKiller(c, e.target.checked) } }), ' ',
                c.is_killer ? el('span', { class: 'killer-badge', text: 'Killer' }) : null)
            : el('td', {}, el('span', { class: 'redacted', text: 'Hidden' }));
        body.appendChild(el('tr', {},
            el('th', { text: c.display_name || 'Unnamed', attrs: { scope: 'row' } }),
            el('td', { text: c.player_name || '' }),
            el('td', { class: 'role-tag', text: c.role_key }),
            el('td', {}, c.lifepath_complete ? badge('Complete', 'ok') : badge('Incomplete', 'danger')),
            killerCell,
            el('td', {},
                el('a', { text: lobby ? 'Edit Lifepath' : 'View Lifepath', attrs: { href: `build.html?session=${sid}&char=${encodeURIComponent(c.id)}` } }),
                ' | ',
                el('a', { text: 'View Sheet', attrs: { href: `character.html?session=${sid}&char=${encodeURIComponent(c.id)}` } }))));
    });
    section.appendChild(el('div', { class: 'table-scroll' }, el('table', {},
        el('thead', {}, el('tr', {},
            ['Character', 'Player', 'Role', 'Lifepath', 'Killer', 'Actions'].map(h => el('th', { text: h, attrs: { scope: 'col' } })))),
        body)));
    if (!lobby) {
        section.appendChild(el('p', { class: 'muted', text: 'Role, Lifepath and killer are locked while the game is generated. Reset it in the lobby to change them.' }));
    }
    return section;
}

// --- Who knows what ---------------------------------------------------------------------------------

function renderMatrix() {
    const section = el('section', { class: 'panel', attrs: { 'aria-labelledby': 'matrixHeading' } },
        el('h2', { text: 'Who knows what', attrs: { id: 'matrixHeading' } }));
    if (data.secrets.length === 0) {
        renderEmpty(section.appendChild(el('div')), 'No secrets yet.', 'Generate the game from the lobby to see who knows what.');
        return section;
    }
    section.appendChild(el('p', { class: 'muted', text: 'Rows are the people a secret is about; columns are the people who know. Each number is how many of that person\'s secrets the column knows. Select a number to list them.' }));

    const detail = el('div', { class: 'matrix-detail', attrs: { id: 'matrixDetail', 'aria-live': 'polite' } });
    const body = el('tbody');
    data.characters.forEach(subject => {
        const row = el('tr', {}, el('th', { text: subject.display_name, attrs: { scope: 'row' } }));
        data.characters.forEach(knower => {
            if (knower.id === subject.id) {
                row.appendChild(el('td', { class: 'diagonal', attrs: { 'aria-label': 'same character' } }));
                return;
            }
            const ids = data.knowledge_matrix[knower.id]?.[subject.id] || [];
            if (ids.length === 0) {
                row.appendChild(el('td'));
                return;
            }
            const isOpen = openCell && openCell.knower === knower.id && openCell.subject === subject.id;
            row.appendChild(el('td', {}, el('button', {
                class: 'secondary-btn cell-btn', text: ids.length,
                attrs: { type: 'button', 'aria-expanded': String(!!isOpen), 'aria-controls': 'matrixDetail',
                    'aria-label': `${knower.display_name} knows ${ids.length} secret${ids.length === 1 ? '' : 's'} about ${subject.display_name}` },
                on: { click: () => {
                    openCell = isOpen ? null : { knower: knower.id, subject: subject.id };
                    renderAll();
                    const again = document.querySelector('.matrix button[aria-expanded="true"]');
                    if (again) again.focus();
                } } })));
        });
        body.appendChild(row);
    });
    section.appendChild(el('div', { class: 'table-scroll' }, el('table', { class: 'matrix' },
        el('caption', { class: 'visually-hidden', text: 'Number of secrets each column character knows about each row character' }),
        el('thead', {}, el('tr', {}, el('th', { text: 'About \\ Known by', attrs: { scope: 'col' } }),
            data.characters.map(c => el('th', { text: c.display_name, attrs: { scope: 'col' } })))),
        body)));

    if (openCell) {
        const ids = data.knowledge_matrix[openCell.knower]?.[openCell.subject] || [];
        detail.appendChild(el('h3', { text: `${charName(openCell.knower)} knows about ${charName(openCell.subject)}` }));
        ids.forEach(id => {
            const s = data.secrets.find(x => x.id === id);
            if (!s) return;
            detail.appendChild(!killerRevealed && isKillerSecret(s) ? redactedRow()
                : el('div', { class: 'sheet-item' }, el('div', { class: 'item-text', text: s.text_knower || s.text_subject }), secretBadges(s)));
        });
    }
    section.appendChild(detail);
    return section;
}

// --- Per-character panels -----------------------------------------------------------------------------

function lifepathList(character) {
    const lp = character.lifepath;
    const entries = [
        ...lp.shared.map(f => [f.label, f.value]),
        ...lp.factions.flatMap(f => [
            [`${labelize(f.side)} faction`, [f.name, f.type].filter(Boolean).join(' — ')],
            [`${labelize(f.side)} feeling`, f.feeling]]),
        ...lp.role_specific.map(f => [f.label, f.value])
    ];
    const list = el('dl', { class: 'kv-list' });
    entries.forEach(([label, value]) => list.append(el('dt', { text: label }), el('dd', { text: value || '—' })));
    return list;
}

function secretEntry(s, perspective) {
    if (!killerRevealed && isKillerSecret(s)) return redactedRow();
    const knowers = s.knowers.map(k => k.knower_name);
    return el('div', { class: 'sheet-item' },
        el('div', { class: 'item-text' },
            perspective === 'about'
                ? [el('div', { text: s.text_subject }),
                    el('div', { class: 'objective-meta' }, `Known by: ${knowers.length ? knowers.join(', ') : 'nobody'}`),
                    s.text_knower ? el('div', { class: 'objective-meta', text: `Their wording: ${s.text_knower}` }) : null]
                : [el('div', { class: 'muted' }, 'About ', el('strong', { text: s.subject_name })),
                    el('div', { text: s.text_knower || s.text_subject })]),
        secretBadges(s));
}

function objectiveEntry(o, byId) {
    if (!killerRevealed && KILLER_ONLY_OBJECTIVES.has(o.kind)) {
        return el('div', { class: 'objective-row' }, el('span', { class: 'points-badge', text: '—' }), el('div', { class: 'objective-body' }, redactedRow()));
    }
    const body = el('div', { class: 'objective-body' }, el('div', { text: o.text }));
    const partner = byId.get(o.linked_objective_id);
    if (partner) {
        const verb = o.link_type === 'opposed' ? 'opposes' : 'aligned with';
        const preview = partner.text.length > 90 ? `${partner.text.substring(0, 87)}...` : partner.text;
        body.appendChild(el('div', { class: 'objective-meta', text: `↔ ${verb} ${partner.character_name || 'Unknown'}'s '${preview}'` }));
    }
    if (o.kind === 'frame') {
        body.appendChild(el('div', { class: 'objective-meta', text: `Scapegoat: ${o.subject_name || 'Unknown'}` }));
    }
    if (o.gm_hint) {
        body.appendChild(el('details', { class: 'objective-meta' }, el('summary', { text: 'GM hint' }), o.gm_hint));
    }
    return el('div', { class: 'objective-row' }, pointsBadge(o.points), body,
        el('span', { class: 'objective-meta', text: labelize(o.kind) }));
}

function characterPanel(c, index) {
    const aboutThem = data.secrets.filter(s => s.subject_character_id === c.id);
    const theyKnow = data.secrets.filter(s => s.knowers.some(k => k.knower_character_id === c.id));
    const rels = data.relationships.filter(r => r.source_character_id === c.id || r.target_character_id === c.id);
    const objectives = data.objectives.filter(o => o.character_id === c.id);
    const byId = new Map(data.objectives.map(o => [o.id, o]));
    const total = objectives.filter(o => killerRevealed || !KILLER_ONLY_OBJECTIVES.has(o.kind)).reduce((sum, o) => sum + o.points, 0);

    const summary = el('summary', {}, el('span', { text: c.display_name || 'Unnamed' }),
        el('span', { class: 'role', text: `${(c.role_key || '').toUpperCase()} · ${pointsLabel(total)}${killerRevealed ? '' : '*'}` }),
        killerRevealed && c.is_killer ? el('span', { class: 'killer-badge', text: 'Killer' }) : null);

    const panel = el('details', { class: 'character-panel', dataset: { characterId: c.id }, attrs: { open: openPanels.has(c.id) || (index === 0 && openPanels.size === 0 && !data.initialised) } },
        summary,
        el('p', { class: 'muted', text: `Player: ${c.player_name || 'unknown'}` }),
        el('h3', { text: 'Description & traits' }),
        c.description ? el('p', { text: c.description }) : el('p', { class: 'muted', text: 'No description yet.' }),
        c.traits.length ? el('div', { class: 'item-badges' }, c.traits.map(t => badge(t, 'neutral'))) : el('p', { class: 'muted', text: 'No traits yet.' }),
        el('h3', { text: 'Lifepath' }), lifepathList(c),
        el('h3', { text: 'Secrets about them' }),
        aboutThem.length ? aboutThem.map(s => secretEntry(s, 'about')) : el('p', { class: 'muted', text: 'No secrets generated.' }),
        el('h3', { text: 'What they know' }),
        theyKnow.length ? theyKnow.map(s => secretEntry(s, 'known')) : el('p', { class: 'muted', text: 'They know nothing yet.' }),
        el('h3', { text: 'Relationships' }),
        rels.length ? rels.map(r => {
            const mine = r.source_character_id === c.id;
            return el('div', { class: 'sheet-item' },
                el('div', { class: 'item-text' }, el('strong', { text: (mine ? r.target_name : r.source_name) || 'Unknown' }),
                    el('div', { text: (mine ? r.description_source : r.description_target) || '' })),
                el('div', { class: 'item-badges' }, badge(labelize(r.rel_type), 'info')));
        }) : el('p', { class: 'muted', text: 'No relationships generated.' }),
        el('h3', { text: 'Objectives' }),
        objectives.length ? objectives.map(o => objectiveEntry(o, byId)) : el('p', { class: 'muted', text: 'No objectives generated.' }),
        killerRevealed ? null : el('p', { class: 'muted', text: '* Point totals exclude hidden killer-only objectives.' }));

    panel.addEventListener('toggle', () => { if (panel.open) openPanels.add(c.id); else openPanels.delete(c.id); data.initialised = true; });
    return panel;
}

function renderPanels() {
    const section = el('section', { attrs: { 'aria-labelledby': 'panelsHeading' } },
        el('h2', { text: 'Characters in detail', attrs: { id: 'panelsHeading' } }));
    if (data.characters.length === 0) {
        renderEmpty(section.appendChild(el('div')), 'No characters yet.');
        return section;
    }
    data.characters.forEach((c, i) => section.appendChild(characterPanel(c, i)));
    return section;
}

// --- Page -----------------------------------------------------------------------------------------------

function renderAll() {
    clear(content);
    content.append(renderHeader(), renderCharactersTable(), renderMatrix(), renderPanels());
}

async function reload() {
    try {
        data = { ...(await apiFetch(`/sessions/${sid}/master`)), initialised: data ? data.initialised : false };
        renderAll();
    } catch (err) {
        showAlert(err.message);
    }
}

// Print: every panel open (and restored afterwards)
window.addEventListener('beforeprint', () => {
    printOpened = [...document.querySelectorAll('details:not([open])')];
    printOpened.forEach(d => { d.open = true; });
});
window.addEventListener('afterprint', () => {
    printOpened.forEach(d => { d.open = false; });
    printOpened = [];
});

async function init() {
    if (!sessionId) {
        window.location.href = 'dashboard.html';
        return;
    }
    const user = await requireUser();
    if (!user) return;
    document.getElementById('backLink').href = `lobby.html?id=${sid}`;

    await loadInto(content, () => apiFetch(`/sessions/${sid}/master`), master => {
        data = { ...master, initialised: false };
        renderAll();
    }, 'Loading master sheet...');
}

init();
