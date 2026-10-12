// Shared UI building blocks used by every page: busy buttons, confirm dialogs, loading /
// error / empty states, badges, the locked-game banner and clipboard copy.
import { el, clear, showAlert, hideAlert } from './dom.js';

export { showAlert, hideAlert };

export function getParam(name) {
    return new URLSearchParams(window.location.search).get(name);
}

export function labelize(value) {
    return String(value || '').replace(/_/g, ' ');
}

export function pointsLabel(points) {
    return `${points} ${points === 1 ? 'pt' : 'pts'}`;
}

export function formatDateTime(iso) {
    if (!iso) return '';
    const date = new Date(iso.endsWith('Z') || /[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
    return Number.isNaN(date.getTime()) ? '' : date.toLocaleString();
}

// --- Buttons ------------------------------------------------------------------------------

/** Disables a button while a request is in flight. Returns a function that restores it. */
export function setBusy(button, busyLabel) {
    const original = button.textContent;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    if (busyLabel) button.textContent = busyLabel;
    return () => {
        button.disabled = false;
        button.removeAttribute('aria-busy');
        button.textContent = original;
    };
}

/** Runs `action` with the button busy. Errors become an alert; the form keeps its input. */
export async function withBusy(button, busyLabel, action) {
    const restore = setBusy(button, busyLabel);
    try {
        return await action();
    } finally {
        restore();
    }
}

// --- Dialogs ------------------------------------------------------------------------------

/** Native <dialog>: focus is trapped and Escape cancels. Resolves true on confirm. */
export function confirmDialog(message, { title = 'Are you sure?', confirmLabel = 'Confirm', cancelLabel = 'Cancel', danger = false } = {}) {
    return new Promise(resolve => {
        const previouslyFocused = document.activeElement;
        const titleId = `dialogTitle${Math.random().toString(36).slice(2, 8)}`;
        let confirmed = false;

        const dialog = el('dialog', { class: 'confirm-dialog', attrs: { 'aria-labelledby': titleId } },
            el('form', { attrs: { method: 'dialog' } },
                el('h2', { text: title, attrs: { id: titleId } }),
                el('p', { text: message }),
                el('div', { class: 'dialog-actions' },
                    el('button', { text: cancelLabel, class: 'secondary-btn', attrs: { type: 'button', value: 'cancel' },
                        on: { click: () => dialog.close() } }),
                    el('button', { text: confirmLabel, class: danger ? 'danger-btn' : '', attrs: { type: 'submit', value: 'confirm' },
                        on: { click: () => { confirmed = true; } } }))));

        dialog.addEventListener('close', () => {
            dialog.remove();
            if (previouslyFocused && typeof previouslyFocused.focus === 'function' && document.contains(previouslyFocused)) {
                previouslyFocused.focus();
            }
            resolve(confirmed);
        });

        document.body.appendChild(dialog);
        dialog.showModal();
        // Destructive actions start on Cancel so Enter cannot confirm by accident
        const first = danger ? dialog.querySelector('.secondary-btn') : dialog.querySelector('[type="submit"]');
        first.focus();
    });
}

// --- Page states --------------------------------------------------------------------------

export function renderLoading(container, label = 'Loading...') {
    clear(container).appendChild(el('p', { class: 'state-message loading', text: label, attrs: { role: 'status' } }));
}

export function renderEmpty(container, message, hint) {
    clear(container).appendChild(el('div', { class: 'state-message empty' },
        el('p', { text: message }),
        hint ? el('p', { class: 'muted', text: hint }) : null));
}

export function renderError(container, message, onRetry) {
    clear(container).appendChild(el('div', { class: 'state-message error-state', attrs: { role: 'alert' } },
        el('p', { text: message }),
        onRetry ? el('button', { class: 'secondary-btn retry-btn', text: 'Try again', attrs: { type: 'button' },
            on: { click: onRetry } }) : null));
}

/**
 * Shows "Loading...", runs `load`, then `render(data)`. A failure shows the message and a
 * retry button instead of a blank section. Never throws.
 */
export async function loadInto(container, load, render, loadingLabel) {
    renderLoading(container, loadingLabel);
    try {
        const data = await load();
        clear(container);
        render(data, container);
    } catch (err) {
        renderError(container, err.message || 'Something went wrong.', () => loadInto(container, load, render, loadingLabel));
    }
}

// --- Small components ---------------------------------------------------------------------

/** Text always accompanies the colour, so badges never rely on colour alone. */
export function badge(text, kind = 'neutral') {
    return el('span', { class: `badge badge-${kind}`, text });
}

export function pointsBadge(points) {
    return el('span', { class: 'points-badge', text: pointsLabel(points) });
}

/** The one banner shown wherever role / Lifepath / killer are frozen (§7.3). */
export function lockedBanner({ isGm, sessionId }) {
    return el('div', { class: 'locked-banner', attrs: { role: 'note' } },
        el('strong', { text: 'Locked' }),
        ' \u2014 the game has been generated. The GM can reset the game in the lobby to unlock role, Lifepath and killer.',
        isGm ? el('span', {}, ' ', el('a', { text: 'Go to the lobby', attrs: { href: `lobby.html?id=${encodeURIComponent(sessionId)}` } })) : null);
}

/** Copies text to the clipboard and tells the user (and screen readers) it worked. */
export async function copyText(text, button, doneLabel = 'Copied!') {
    let ok = false;
    try {
        await navigator.clipboard.writeText(text);
        ok = true;
    } catch (e) {
        // Fallback for insecure contexts: select a temporary field and copy
        const temp = el('textarea', { attrs: { readonly: true, 'aria-hidden': 'true' } });
        temp.value = text;
        temp.style.position = 'fixed';
        temp.style.opacity = '0';
        document.body.appendChild(temp);
        temp.select();
        try { ok = document.execCommand('copy'); } catch (err) { ok = false; }
        temp.remove();
    }
    if (!ok) {
        showAlert('Could not copy automatically. Select the text and copy it manually.', 'error');
        return false;
    }
    if (button) {
        const original = button.textContent;
        button.textContent = doneLabel;
        setTimeout(() => { button.textContent = original; }, 1800);
    }
    showAlert(`${doneLabel}`, 'success');
    return true;
}

/** A copyable value with a visible button, e.g. join code or seed. */
export function copyButton(value, label = 'Copy') {
    const button = el('button', { class: 'secondary-btn small-btn', text: label, attrs: { type: 'button', 'aria-label': `${label} ${value}` } });
    button.addEventListener('click', () => copyText(String(value), button));
    return button;
}
