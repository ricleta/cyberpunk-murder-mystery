import { requireUser } from '../auth.js';
import { apiFetch } from '../api.js';
import { el, clear, setText, showAlert, hideAlert } from '../dom.js';
import { downloadJson, slugify } from '../files.js';
import { openImportDialog } from '../import-dialog.js';
import { printHeader, printFooter, relationshipItem, secretItem } from '../sheet-render.js';
import {
    confirmDialog, getParam, loadInto, pointsBadge, pointsLabel, renderEmpty, renderError,
    renderLoading, withBusy
} from '../ui.js';

const sessionId = getParam('session');
const charId = getParam('char');
const sid = encodeURIComponent(sessionId || '');
const cid = encodeURIComponent(charId || '');
const objectivesUrl = `/sessions/${sid}/objectives`;

const NAME_MAX = 50;
const DESCRIPTION_MAX = 4000;
const TRAIT_MAX = 60;
const TRAITS_MAX_COUNT = 20;
const OBJECTIVE_MAX = 500;

let isGm = false;
let character = null;
let session = null;

const $ = id => document.getElementById(id);

// --- Inline validation -------------------------------------------------------------------------

function fieldError(inputId, errorId, message) {
    const error = $(errorId);
    error.textContent = message || '';
    error.hidden = !message;
    const input = $(inputId);
    if (message) input.setAttribute('aria-invalid', 'true'); else input.removeAttribute('aria-invalid');
    return !message;
}

function parseTraits() {
    return $('traits').value.split(',').map(t => t.trim()).filter(Boolean);
}

function validateSheetFields() {
    const name = $('displayName').value.trim();
    const traits = parseTraits();
    const okName = fieldError('displayName', 'nameError',
        !name ? 'A character name is required.' : name.length > NAME_MAX ? `Keep the name to ${NAME_MAX} characters.` : '');
    const okDescription = fieldError('description', 'descriptionError',
        $('description').value.length > DESCRIPTION_MAX ? `Keep the description to ${DESCRIPTION_MAX} characters.` : '');
    const okTraits = fieldError('traits', 'traitsError',
        traits.length > TRAITS_MAX_COUNT ? `Use at most ${TRAITS_MAX_COUNT} traits.`
            : traits.some(t => t.length > TRAIT_MAX) ? `Each trait must be ${TRAIT_MAX} characters or fewer.` : '');
    return okName && okDescription && okTraits;
}

// --- Print mirrors -------------------------------------------------------------------------------
// Textareas print as clipped boxes, so print-only elements mirror their current value.

function syncPrintMirrors() {
    $('printDescription').textContent = $('description').value;
    const traits = parseTraits();
    $('printTraits').textContent = traits.join(', ');
    $('printName').textContent = $('displayName').value;
}

function renderPrintFrame() {
    if (!character) return;
    const holderTop = clear($('printHeaderSlot'));
    holderTop.appendChild(printHeader({
        sessionName: session.name, characterName: character.display_name, role: character.role_key,
        date: new Date().toLocaleDateString()
    }));
    clear($('printFooterSlot')).appendChild(printFooter(character.display_name));
}

['displayName', 'description', 'traits'].forEach(id => $(id).addEventListener('input', syncPrintMirrors));
window.addEventListener('beforeprint', () => { syncPrintMirrors(); renderPrintFrame(); });
$('printBtn').addEventListener('click', () => window.print());

// Backup / restore (works in any session status; the import report explains locked fields)
$('exportBtn').addEventListener('click', async () => {
    hideAlert();
    await withBusy($('exportBtn'), 'Exporting...', async () => {
        try {
            const file = await apiFetch(`/sessions/${sid}/characters/${cid}/export`);
            downloadJson(`cyberpunk-character-${slugify(file.character.display_name)}.json`, file);
        } catch (err) {
            showAlert(err.message);
        }
    });
});

$('importBtn').addEventListener('click', () => {
    hideAlert();
    openImportDialog({ mode: 'existing', sessionId, characterId: charId, onDone: () => init() });
});

// --- Description / traits ----------------------------------------------------------------------

function fillSheet() {
    setText('charNameDisplay', character.display_name || 'Unnamed');
    document.title = `${character.display_name || 'Character'} - Character Sheet`;
    $('displayName').value = character.display_name || '';
    setText('roleDisplay', (character.role_key || 'unknown').toUpperCase());
    $('description').value = character.description || '';
    $('traits').value = (character.traits || []).join(', ');
    syncPrintMirrors();
    renderPrintFrame();

    const locked = session.status !== 'lobby';
    const lifepathLink = $('editLifepathLink');
    lifepathLink.href = `build.html?session=${sid}&char=${cid}`;
    lifepathLink.textContent = locked ? 'View Lifepath (locked)' : 'Edit Lifepath';

    const compose = $('composeBtn');
    compose.disabled = !character.lifepath_complete;
    $('composeHint').textContent = character.lifepath_complete
        ? 'Suggests a description and traits from your Lifepath. Nothing is saved until you click Save changes.'
        : 'Finish your Lifepath first: Compose needs every Lifepath choice.';
}

$('saveBtn').addEventListener('click', async () => {
    hideAlert();
    if (!validateSheetFields()) return;
    const payload = {
        display_name: $('displayName').value.trim(),
        description: $('description').value,
        traits: parseTraits()
    };
    await withBusy($('saveBtn'), 'Saving...', async () => {
        try {
            await apiFetch(`/sessions/${sid}/characters/${cid}`, { method: 'PATCH', body: JSON.stringify(payload) });
            character.display_name = payload.display_name;
            setText('charNameDisplay', payload.display_name);
            renderPrintFrame();
            showAlert('Saved successfully!', 'success');
            // Generated text shows the new name everywhere: refresh what depends on it
            loadSecrets();
            loadRelationships();
            loadObjectives();
        } catch (err) {
            showAlert(err.message);   // the inputs keep what was typed
        }
    });
});

$('composeBtn').addEventListener('click', async () => {
    hideAlert();
    const hasContent = $('description').value.trim() || $('traits').value.trim();
    if (hasContent) {
        const ok = await confirmDialog('This replaces the description and traits currently in the form with a new suggestion. Nothing is saved until you click Save changes.',
            { title: 'Replace your description and traits?', confirmLabel: 'Replace' });
        if (!ok) return;
    }
    await withBusy($('composeBtn'), 'Composing...', async () => {
        try {
            const suggestion = await apiFetch(`/sessions/${sid}/characters/${cid}/compose`, { method: 'POST' });
            $('description').value = suggestion.description;
            $('traits').value = suggestion.traits.join(', ');
            syncPrintMirrors();
            validateSheetFields();
            showAlert('Suggestion filled in. Edit it if you like, then click Save changes to keep it.', 'info');
        } catch (err) {
            showAlert(err.message);
        }
    });
});

// --- Secrets -----------------------------------------------------------------------------------

function renderSecrets(chars, container) {
    const secrets = chars.secrets || [];
    if (secrets.length === 0) {
        renderEmpty(container, 'No secrets generated yet.', 'Secrets appear here once the GM generates the game.');
        return;
    }
    const mine = secrets.filter(s => s.about_self);
    const known = secrets.filter(s => !s.about_self);
    container.appendChild(el('h3', { text: 'Your secrets' }));
    if (mine.length) mine.forEach(s => container.appendChild(secretItem(s)));
    else container.appendChild(el('p', { class: 'muted', text: 'You have no secrets of your own.' }));
    container.appendChild(el('h3', { text: 'What you know about others' }));
    if (known.length) known.forEach(s => container.appendChild(secretItem(s)));
    else container.appendChild(el('p', { class: 'muted', text: 'You do not know anyone else’s secrets.' }));
}

function loadSecrets() {
    return loadInto($('secretsList'),
        () => apiFetch(`/sessions/${sid}/characters/${cid}`),
        renderSecrets, 'Loading secrets...');
}

// --- Relationships -----------------------------------------------------------------------------

function renderRelationships(data, container) {
    const mine = [];
    (data.relationships || []).forEach(r => {
        if (r.source_character_id === charId) {
            mine.push({ other: r.target_name, type: r.rel_type, text: r.description_source });
        } else if (r.target_character_id === charId) {
            mine.push({ other: r.source_name, type: r.rel_type, text: r.description_target });
        }
    });
    if (mine.length === 0) {
        renderEmpty(container, 'No relationships generated yet.', 'Relationships appear here once the GM generates the game.');
        return;
    }
    mine.forEach(r => container.appendChild(relationshipItem(r)));
}

function loadRelationships() {
    return loadInto($('relationshipsList'), () => apiFetch(`/sessions/${sid}/relationships`),
        renderRelationships, 'Loading relationships...');
}

// --- Objectives --------------------------------------------------------------------------------

function pointsSelect(selected) {
    const select = el('select', { attrs: { 'aria-label': 'Points' } });
    [1, 2, 3].forEach(n => select.appendChild(el('option', { text: pointsLabel(n), attrs: { value: n, selected: n === selected } })));
    return select;
}

function smallButton(label, onClick, extraClass = '', ariaLabel) {
    return el('button', { class: `secondary-btn small-btn ${extraClass}`.trim(), text: label,
        attrs: { type: 'button', 'aria-label': ariaLabel }, on: { click: onClick } });
}

function validateObjectiveText(text) {
    const trimmed = text.trim();
    if (!trimmed) return 'Enter the objective text.';
    if (trimmed.length > OBJECTIVE_MAX) return `Keep it to ${OBJECTIVE_MAX} characters (now ${trimmed.length}).`;
    return '';
}

function objectiveRow(o) {
    const body = el('div', { class: 'objective-body' }, el('div', { text: o.text }));
    if (o.gm_hint) {   // gm_hint is only ever sent to the GM
        body.appendChild(el('details', { class: 'objective-meta gm-only' }, el('summary', { text: 'GM hint' }), o.gm_hint));
    }
    const row = el('div', { class: 'objective-row' }, pointsBadge(o.points), body,
        smallButton('Edit', () => row.replaceWith(objectiveEditor(o)), 'no-print', `Edit objective: ${o.text}`),
        smallButton('Delete', () => deleteObjective(o), 'danger-btn no-print', `Delete objective: ${o.text}`));
    return row;
}

function objectiveEditor(o) {
    const input = el('input', { attrs: { type: 'text', maxlength: String(OBJECTIVE_MAX + 50), 'aria-label': 'Objective text' } });
    input.value = o.text;
    const select = pointsSelect(o.points);
    const hint = isGm ? el('input', { class: 'gm-only', attrs: { type: 'text', maxlength: '500', placeholder: 'GM hint', 'aria-label': 'GM hint' } }) : null;
    if (hint) hint.value = o.gm_hint || '';
    const error = el('p', { class: 'field-error', attrs: { role: 'alert', hidden: true } });
    const save = smallButton('Save', async () => {
        const problem = validateObjectiveText(input.value);
        error.textContent = problem;
        error.hidden = !problem;
        if (problem) { input.setAttribute('aria-invalid', 'true'); input.focus(); return; }
        input.removeAttribute('aria-invalid');

        // Send only what changed (the server keeps stored name tokens for unchanged text)
        const payload = {};
        if (input.value.trim() !== o.text) payload.text = input.value.trim();
        const points = parseInt(select.value, 10);
        if (points !== o.points) payload.points = points;
        if (hint && hint.value.trim() !== (o.gm_hint || '')) payload.gm_hint = hint.value.trim();
        if (Object.keys(payload).length === 0) { row.replaceWith(objectiveRow(o)); return; }

        await withBusy(save, 'Saving...', async () => {
            try {
                await apiFetch(`${objectivesUrl}/${encodeURIComponent(o.id)}`, { method: 'PATCH', body: JSON.stringify(payload) });
                await loadObjectives();
            } catch (err) {
                error.textContent = err.message;   // input stays so nothing is lost
                error.hidden = false;
            }
        });
    });
    const row = el('div', { class: 'objective-row objective-editor' }, input, select, hint, save,
        smallButton('Cancel', () => row.replaceWith(objectiveRow(o))), error);
    return row;
}

async function deleteObjective(o) {
    const ok = await confirmDialog(`Delete this objective? "${o.text}"`, { title: 'Delete objective', confirmLabel: 'Delete', danger: true });
    if (!ok) return;
    try {
        await apiFetch(`${objectivesUrl}/${encodeURIComponent(o.id)}`, { method: 'DELETE' });
        await loadObjectives();
    } catch (err) {
        showAlert(err.message);
    }
}

function renderObjectives(data, container) {
    if (data.objectives.length === 0) {
        renderEmpty(container, 'No objectives yet.', 'Objectives appear once the game is generated. You can also add your own below.');
        return;
    }
    data.objectives.forEach(o => container.appendChild(objectiveRow(o)));
}

function loadObjectives() {
    // Players always get only their own objectives; the character filter is for the GM.
    return loadInto($('objectivesList'), () => apiFetch(`${objectivesUrl}?character_id=${cid}`),
        renderObjectives, 'Loading objectives...');
}

$('addObjectiveForm').addEventListener('submit', async e => {
    e.preventDefault();
    hideAlert();
    const input = $('newObjectiveText');
    const problem = validateObjectiveText(input.value);
    const error = $('newObjectiveError');
    error.textContent = problem;
    error.hidden = !problem;
    if (problem) { input.setAttribute('aria-invalid', 'true'); input.focus(); return; }
    input.removeAttribute('aria-invalid');
    await withBusy($('addObjectiveBtn'), 'Adding...', async () => {
        try {
            await apiFetch(objectivesUrl, {
                method: 'POST',
                body: JSON.stringify({ character_id: charId, text: input.value.trim(), points: parseInt($('newObjectivePoints').value, 10) })
            });
            input.value = '';
            await loadObjectives();
        } catch (err) {
            showAlert(err.message);
        }
    });
});

// --- Page ----------------------------------------------------------------------------------------

async function init() {
    if (!sessionId || !charId) {
        window.location.href = 'dashboard.html';
        return;
    }
    const user = await requireUser();
    if (!user) return;

    const status = $('sheetStatus');
    renderLoading(status, 'Loading character...');
    try {
        session = await apiFetch(`/sessions/${sid}`);
        isGm = session.gm_id === user.id;
        $('backLink').href = isGm ? `master.html?id=${sid}` : `lobby.html?id=${sid}`;
        $('backLink').textContent = isGm ? 'Back to Master Sheet' : 'Back to Lobby';

        character = await apiFetch(`/sessions/${sid}/characters/${cid}`);
        clear(status);
        $('sheet').hidden = false;
        fillSheet();

        // Each section loads on its own, so one failure does not blank the page
        renderSecrets(character, clear($('secretsList')));
        await Promise.all([loadRelationships(), loadObjectives()]);
    } catch (err) {
        $('sheet').hidden = true;
        setText('charNameDisplay', 'Unavailable');
        renderError(status, err.message, init);
    }
}

init();
