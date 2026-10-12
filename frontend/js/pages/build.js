import { requireUser } from '../auth.js';
import { apiFetch } from '../api.js';
import { el, clear, showAlert, hideAlert } from '../dom.js';
import { confirmDialog, getParam, lockedBanner, renderError, renderLoading } from '../ui.js';

const sessionId = getParam('session');
const charId = getParam('char');
const sid = encodeURIComponent(sessionId || '');
const cid = encodeURIComponent(charId || '');

let character = null;
let session = null;
let isGm = false;
let locked = false;
let lifepathData = { shared: {}, factions: {}, roles: {} };
let allRoles = [];

// Maps DB columns to JSON keys
const sharedFieldsMap = [
    { db: 'cultural_origin', json: 'culturalOrigin' },
    { db: 'personality', json: 'personality' },
    { db: 'clothing_style', json: 'clothingStyle' },
    { db: 'hairstyle', json: 'hairstyle' },
    { db: 'affectation', json: 'affectation' },
    { db: 'value_most', json: 'valueMost' },
    { db: 'feelings_about_people', json: 'feelingsAboutPeople' },
    { db: 'most_valued_person', json: 'mostValuedPerson' },
    { db: 'most_valued_possession', json: 'mostValuedPossession' },
    { db: 'family_background', json: 'familyBackground' },
    { db: 'childhood_environment', json: 'childhoodEnvironment' },
    { db: 'family_crisis', json: 'familyCrisis' },
    { db: 'life_goals', json: 'lifeGoals' }
];

document.getElementById('backLink').href = `lobby.html?id=${sid}`;
document.getElementById('viewSheetLink').href = `character.html?session=${sid}&char=${cid}`;

function rollOption(options) {
    if (!options || options.length === 0) return null;
    return options[Math.floor(Math.random() * options.length)].id;
}

// Returns true when the server accepted the change
async function saveField(payload) {
    hideAlert();
    try {
        const res = await apiFetch(`/sessions/${sid}/characters/${cid}`, { method: 'PATCH', body: JSON.stringify(payload) });
        character.lifepath_complete = res.character.lifepath_complete;
        if (res.character.role_specific_lifepath) {
            character.role_specific_lifepath = res.character.role_specific_lifepath;
        }
        updateCompletionTracker();
        return true;
    } catch (err) {
        showAlert(err.message);
        if (err.code === 'session_locked') {
            await loadAll();     // the game was generated meanwhile: show the locked state
        }
        return false;
    }
}

let fieldCounter = 0;

function createFieldRow(label, options, key, currentValue, onChange) {
    const id = `field-${key}-${fieldCounter++}`;
    const container = el('div');

    const select = el('select', { attrs: { id, disabled: locked } },
        el('option', { text: '-- Select --', attrs: { value: '' } }),
        options.map(o => el('option', { text: o.text, attrs: { value: o.id } })));
    select.value = currentValue || '';

    const desc = el('div', { class: 'field-description', attrs: { id: `${id}-desc`, hidden: true } });
    select.setAttribute('aria-describedby', desc.id);
    function updateDesc(val) {
        const opt = options.find(o => o.id === val);
        desc.textContent = opt && opt.description ? opt.description : '';
        desc.hidden = !desc.textContent;
    }
    updateDesc(select.value);

    // A rejected change snaps the select back to the last accepted value
    let stored = currentValue;
    async function commit(value) {
        updateDesc(value);
        const ok = await onChange(value);
        if (ok) {
            stored = value;
        } else {
            select.value = stored || '';
            updateDesc(select.value);
        }
    }

    select.addEventListener('change', e => commit(e.target.value));

    const rollBtn = el('button', { class: 'roll-btn', attrs: { type: 'button', disabled: locked, 'aria-label': `Roll ${label}` } },
        '🎲 Roll');
    rollBtn.addEventListener('click', () => {
        const rolled = rollOption(options);
        if (rolled) {
            select.value = rolled;
            commit(rolled);
        }
    });

    container.appendChild(el('div', { class: 'field-row' },
        el('label', { text: label, attrs: { for: id } }), select, rollBtn));
    container.appendChild(desc);
    return container;
}

function createTextInputRow(label, key, currentValue, onChange) {
    const id = `field-${key}-${fieldCounter++}`;
    const input = el('input', { attrs: { type: 'text', id, maxlength: '60', autocomplete: 'off', disabled: locked } });
    input.value = currentValue || '';
    // Debounced save; a failed save leaves what was typed in place
    let timeout = null;
    input.addEventListener('input', e => {
        clearTimeout(timeout);
        timeout = setTimeout(() => onChange(e.target.value), 500);
    });
    return el('div', { class: 'field-row' }, el('label', { text: label, attrs: { for: id } }), input);
}

function factionColumn(container, side, label) {
    clear(container);
    const typeKey = `${side}_faction_type`;
    const nameKey = `${side}_faction_name`;
    const feelKey = `${side}_faction_feeling`;
    const feelings = lifepathData.factions.feelings[`${side}Feelings`].options;
    const set = (key) => async (val) => {
        const previous = character[key];
        character[key] = val;
        const ok = await saveField({ [key]: val });
        if (!ok) character[key] = previous;
        return ok;
    };
    container.append(
        createFieldRow('Faction Type', lifepathData.factions.types.options, typeKey, character[typeKey], set(typeKey)),
        createTextInputRow('Faction Name', nameKey, character[nameKey], set(nameKey)),
        createFieldRow('Feeling', feelings, feelKey, character[feelKey], set(feelKey)));
}

function renderBuilder() {
    const content = document.getElementById('builderContent');
    if (!character.role_key) {
        content.hidden = true;
        updateCompletionTracker();
        return;
    }
    content.hidden = false;

    const sharedCont = clear(document.getElementById('sharedFieldsContainer'));
    sharedFieldsMap.forEach(f => {
        const tableData = lifepathData.shared[f.json];
        if (!tableData) return;
        sharedCont.appendChild(createFieldRow(tableData.label, tableData.options, f.db, character[f.db], async val => {
            const previous = character[f.db];
            character[f.db] = val;
            const ok = await saveField({ [f.db]: val });
            if (!ok) character[f.db] = previous;
            return ok;
        }));
    });

    factionColumn(document.getElementById('friendFactionContainer'), 'friend');
    factionColumn(document.getElementById('enemyFactionContainer'), 'enemy');

    const specificCont = clear(document.getElementById('roleSpecificContainer'));
    const roleData = lifepathData.roles[character.role_key];
    if (roleData && roleData.tables) {
        Object.keys(roleData.tables).forEach(tableKey => {
            const table = roleData.tables[tableKey];
            const currentVal = (character.role_specific_lifepath || {})[tableKey];
            specificCont.appendChild(createFieldRow(table.label, table.options, tableKey, currentVal, async val => {
                const ok = await saveField({ role_specific_lifepath: { [tableKey]: val } });
                if (ok) character.role_specific_lifepath = { ...(character.role_specific_lifepath || {}), [tableKey]: val };
                return ok;
            }));
        });
    }

    updateCompletionTracker();
}

function updateCompletionTracker() {
    const tracker = document.getElementById('completionTracker');
    if (!character.role_key) {
        tracker.textContent = 'Please select a role first';
        tracker.className = 'incomplete';
        return;
    }

    let total = sharedFieldsMap.length + 6;
    let filled = sharedFieldsMap.filter(f => character[f.db]).length;
    ['friend', 'enemy'].forEach(side => {
        ['type', 'name', 'feeling'].forEach(part => { if (character[`${side}_faction_${part}`]) filled++; });
    });

    const roleData = lifepathData.roles[character.role_key];
    if (roleData && roleData.tables) {
        const keys = Object.keys(roleData.tables);
        total += keys.length;
        keys.forEach(k => { if (character.role_specific_lifepath && character.role_specific_lifepath[k]) filled++; });
    }

    // The text carries the state; colour only reinforces it
    tracker.textContent = `${filled} / ${total} fields complete${character.lifepath_complete ? ' — READY' : ''}`;
    tracker.className = character.lifepath_complete ? 'complete' : 'incomplete';
}

function applyLock() {
    locked = session.status !== 'lobby';
    const holder = clear(document.getElementById('lockedBanner'));
    if (locked) holder.appendChild(lockedBanner({ isGm, sessionId }));
    document.getElementById('rolePicker').disabled = locked;
}

document.getElementById('rolePicker').addEventListener('change', async e => {
    const picker = e.target;
    const newRole = picker.value;
    if (!newRole || locked) {
        picker.value = character.role_key || '';
        return;
    }
    if (character.role_key && character.role_key !== newRole) {
        const confirmChange = await confirmDialog(
            'Changing your role clears your existing role-specific Lifepath choices.',
            { title: 'Change role?', confirmLabel: 'Change role', danger: true });
        if (!confirmChange) {
            picker.value = character.role_key;
            return;
        }
    }
    const ok = await saveField({ role_key: newRole });
    if (!ok) {
        picker.value = character.role_key || '';
        return;
    }
    await loadCharacter();   // refresh to get the cleared role-specific picks
});

async function loadCharacter() {
    character = await apiFetch(`/sessions/${sid}/characters/${cid}`);
    document.getElementById('rolePicker').value = character.role_key || '';
    renderBuilder();
}

async function loadAll() {
    session = await apiFetch(`/sessions/${sid}`);
    applyLock();
    await loadCharacter();
}

async function init() {
    if (!sessionId || !charId) {
        window.location.href = 'dashboard.html';
        return;
    }
    const user = await requireUser();
    if (!user) return;

    const status = document.getElementById('builderStatus');
    renderLoading(status, 'Loading your Lifepath...');
    try {
        const [roles, shared, types, feelings, roleTables, sess] = await Promise.all([
            fetch('data/roles.json').then(r => r.json()),
            fetch('data/lifepathShared.json').then(r => r.json()),
            fetch('data/factionTypes.json').then(r => r.json()),
            fetch('data/factionFeelings.json').then(r => r.json()),
            fetch('data/rolesLifepath.json').then(r => r.json()),
            apiFetch(`/sessions/${sid}`)
        ]);
        allRoles = roles;
        lifepathData = { shared, factions: { types, feelings }, roles: roleTables };
        session = sess;
        isGm = session.gm_id === user.id;

        const picker = document.getElementById('rolePicker');
        allRoles.forEach(role => picker.appendChild(el('option', { text: role.label, attrs: { value: role.key } })));

        applyLock();
        await loadCharacter();
        clear(status);
    } catch (err) {
        document.getElementById('builderContent').hidden = true;
        renderError(status, `Failed to load the builder: ${err.message}`, init);
    }
}

init();
