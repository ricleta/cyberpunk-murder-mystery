// "Restore from a file": choose a character file (or one character from a bundle), preview
// exactly what would change, then apply. The server does all validation; this only shows
// its report. Everything is built with el().
import { apiFetch } from './api.js';
import { el, clear, showAlert } from './dom.js';
import { MAX_CHARACTER_FILE_BYTES, pickJsonFile } from './files.js';
import { badge, setBusy } from './ui.js';

const FORMAT_CHARACTER = 'cyberpunk-murder-mystery/character';
const FORMAT_BUNDLE = 'cyberpunk-murder-mystery/character-bundle';

const STATUS = {
    apply: { label: 'Will change', kind: 'ok' },
    unchanged: { label: 'Unchanged', kind: 'neutral' },
    skipped: { label: 'Skipped', kind: 'warn' },
};

/**
 * mode 'existing': import into characterId. mode 'create': create the player's character.
 * onDone(result) runs after a successful apply.
 */
export function openImportDialog({ mode, sessionId, characterId, onDone }) {
    const sid = encodeURIComponent(sessionId);
    const base = mode === 'create'
        ? `/sessions/${sid}/characters/import`
        : `/sessions/${sid}/characters/${encodeURIComponent(characterId)}/import`;
    const previouslyFocused = document.activeElement;

    let fileData = null;       // a single-character file
    let report = null;
    let includeText = true;
    let busy = false;

    const body = el('div', { class: 'import-body' });
    const errorBox = el('div', { attrs: { 'aria-live': 'polite' } });
    const titleId = `importTitle${Math.random().toString(36).slice(2, 8)}`;
    const actions = el('div', { class: 'dialog-actions' });
    const dialog = el('dialog', { class: 'confirm-dialog import-dialog', attrs: { 'aria-labelledby': titleId } },
        el('h2', { text: mode === 'create' ? 'Restore your character from a file' : 'Import a character file', attrs: { id: titleId } }),
        errorBox, body, actions);

    dialog.addEventListener('close', () => {
        dialog.remove();
        if (previouslyFocused && typeof previouslyFocused.focus === 'function' && document.contains(previouslyFocused)) {
            previouslyFocused.focus();
        }
    });

    function showError(message) {
        clear(errorBox).appendChild(el('div', { class: 'alert alert-error', attrs: { role: 'alert' }, text: message }));
    }

    function clearError() {
        clear(errorBox);
    }

    function closeButton(label = 'Cancel') {
        return el('button', { class: 'secondary-btn', text: label, attrs: { type: 'button' }, on: { click: () => dialog.close() } });
    }

    // --- Step 1: choose a file -------------------------------------------------------------------
    function stepChoose() {
        clear(body).append(
            el('p', { text: mode === 'create'
                ? 'Choose a character file you exported earlier. A new character is created from it; you can review everything first.'
                : 'Choose a character file you exported earlier. You will see what would change before anything is saved.' }),
            el('p', { class: 'muted', text: 'Files hold your description, traits and Lifepath only. Never secrets, relationships or objectives.' }));
        const choose = el('button', { text: 'Choose file...', attrs: { type: 'button', id: 'importChooseBtn' } });
        choose.addEventListener('click', async () => {
            clearError();
            try {
                const data = await pickJsonFile();
                if (!data) return;
                handleFile(data);
            } catch (err) {
                showError(err.message);
            }
        });
        clear(actions).append(closeButton(), choose);
        choose.focus();
    }

    function handleFile(data) {
        if (data && data.format === FORMAT_BUNDLE && Array.isArray(data.characters)) {
            stepPickFromBundle(data);
        } else if (data && data.format === FORMAT_CHARACTER) {
            if (JSON.stringify(data).length > MAX_CHARACTER_FILE_BYTES) {
                showError('That file is too large to be a character file (limit 64 KB).');
                return;
            }
            fileData = data;
            preview();
        } else {
            showError('That is not a character file from this app.');
        }
    }

    // --- Step 1b: pick one character out of a bundle ----------------------------------------------------
    function stepPickFromBundle(bundle) {
        if (bundle.characters.length === 0) {
            showError('That bundle has no characters in it.');
            return;
        }
        const select = el('select', { attrs: { id: 'bundlePick' } });
        bundle.characters.forEach((c, i) => {
            const name = c && typeof c.display_name === 'string' ? c.display_name : 'Unnamed';
            const role = c && c.role && typeof c.role.text === 'string' ? c.role.text : 'no role';
            select.appendChild(el('option', { text: `${name} — ${role}`, attrs: { value: String(i) } }));
        });
        clear(body).append(
            el('p', { text: `This file holds ${bundle.characters.length} characters${bundle.session_name ? ` from "${bundle.session_name}"` : ''}. Choose one.` }),
            el('label', { text: 'Character', attrs: { for: 'bundlePick' } }), select);
        const use = el('button', { text: 'Use this character', attrs: { type: 'button' } });
        use.addEventListener('click', () => {
            fileData = {
                format: FORMAT_CHARACTER,
                schema_version: bundle.schema_version,
                exported_at: bundle.exported_at,
                character: bundle.characters[parseInt(select.value, 10)],
            };
            preview();
        });
        clear(actions).append(closeButton(), use);
        select.focus();
    }

    // --- Step 2: the dry-run report -----------------------------------------------------------------------
    async function call(dryRun) {
        return apiFetch(`${base}?dry_run=${dryRun ? 1 : 0}&include_text=${includeText ? 1 : 0}`,
            { method: 'POST', body: JSON.stringify(fileData) });
    }

    async function preview() {
        clearError();
        busy = true;
        clear(body).appendChild(el('p', { class: 'state-message', text: 'Checking the file...', attrs: { role: 'status' } }));
        clear(actions);
        try {
            report = await call(true);
            stepReport();
        } catch (err) {
            showError(err.message);
            fileData = null;
            stepChoose();
        } finally {
            busy = false;
        }
    }

    function valueCell(value) {
        return value === null || value === undefined || value === '' ? '—' : String(value);
    }

    function stepReport() {
        const { summary } = report;
        const toggle = el('input', { attrs: { type: 'checkbox', id: 'importIncludeText', checked: includeText } });
        toggle.addEventListener('change', () => { includeText = toggle.checked; preview(); });

        const rows = el('tbody');
        report.fields.forEach(f => {
            const status = STATUS[f.status] || STATUS.skipped;
            rows.appendChild(el('tr', {},
                el('th', { text: f.label, attrs: { scope: 'row' } }),
                el('td', {}, f.status === 'unchanged' ? valueCell(f.after) : [valueCell(f.before), ' → ', valueCell(f.after)]),
                el('td', {}, badge(status.label, status.kind),
                    f.reason ? el('div', { class: 'objective-meta', text: f.reason }) : null)));
        });

        clear(body).append(...[
            el('p', { text: `${summary.apply} to change, ${summary.unchanged} unchanged, ${summary.skipped} skipped.`, attrs: { role: 'status' } }),
            report.warnings.length
                ? el('ul', { class: 'import-warnings' }, report.warnings.map(w => el('li', { text: w }))) : null,
            el('div', { class: 'table-scroll' }, el('table', { class: 'import-report' },
                el('thead', {}, el('tr', {}, ['Field', 'Change', 'Status'].map(h => el('th', { text: h, attrs: { scope: 'col' } })))),
                rows)),
            el('div', { class: 'import-option' }, toggle,
                el('label', { text: 'Also import description and traits', attrs: { for: 'importIncludeText' } })),
            summary.apply === 0
                ? el('p', { class: 'muted', text: 'Nothing to change: this character already matches the file.' }) : null
        ].filter(Boolean));

        const another = el('button', { class: 'secondary-btn', text: 'Choose another file', attrs: { type: 'button' } });
        another.addEventListener('click', () => { fileData = null; clearError(); stepChoose(); });
        if (summary.apply === 0) {
            clear(actions).append(another, closeButton('Close'));
            return;
        }
        const apply = el('button', { text: 'Apply import', attrs: { type: 'button', id: 'importApplyBtn' } });
        apply.addEventListener('click', async () => {
            if (busy) return;
            busy = true;
            clearError();
            const restore = setBusy(apply, 'Applying...');
            [...actions.querySelectorAll('button'), toggle].forEach(b => { b.disabled = true; });
            try {
                const result = await call(false);
                dialog.close();
                showAlert(mode === 'create' ? 'Character created from the file.' : 'Import applied.', 'success');
                if (onDone) await onDone(result);
            } catch (err) {
                showError(err.message);
                restore();
                [...actions.querySelectorAll('button'), toggle].forEach(b => { b.disabled = false; });
            } finally {
                busy = false;
            }
        });
        clear(actions).append(another, closeButton(), apply);
        apply.focus();
    }

    document.body.appendChild(dialog);
    dialog.showModal();
    stepChoose();
    return dialog;
}
