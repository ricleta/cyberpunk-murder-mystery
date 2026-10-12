import { logout, requireUser } from '../auth.js';
import { apiFetch } from '../api.js';
import { el, clear, showAlert, hideAlert } from '../dom.js';
import { badge, confirmDialog, copyButton, loadInto, renderEmpty, withBusy, labelize } from '../ui.js';

let currentUserId = null;
const listContainer = document.getElementById('mySessions');

function statusBadge(status) {
    const kind = status === 'generated' ? 'ok' : status === 'lobby' ? 'info' : 'neutral';
    return badge(labelize(status || 'lobby'), kind);
}

async function removeSession(session) {
    const message = session.is_gm
        ? `Delete "${session.name}" and all its characters? This cannot be undone.`
        : `Leave "${session.name}"? Your character will be deleted.`;
    const confirmed = await confirmDialog(message, {
        title: session.is_gm ? 'Delete session' : 'Leave session',
        confirmLabel: session.is_gm ? 'Delete session' : 'Leave session',
        danger: true
    });
    if (!confirmed) return;
    try {
        if (session.is_gm) {
            await apiFetch(`/sessions/${encodeURIComponent(session.id)}`, { method: 'DELETE' });
        } else {
            await apiFetch(`/sessions/${encodeURIComponent(session.id)}/members/${encodeURIComponent(currentUserId)}`, { method: 'DELETE' });
        }
        await loadSessions();
    } catch (err) {
        showAlert(err.message);
    }
}

function renderSessions(data, container) {
    if (data.sessions.length === 0) {
        renderEmpty(container, "You haven't joined any sessions yet.",
            'Enter a join code from your GM above, or create a new session to run a game.');
        return;
    }
    const list = el('ul', { class: 'plain-list' });
    data.sessions.forEach(s => {
        list.appendChild(el('li', { class: 'row-item' },
            el('a', { class: 'session-link', attrs: { href: `lobby.html?id=${encodeURIComponent(s.id)}` } },
                el('span', { class: 'session-name', text: s.name }),
                el('span', { class: 'muted', text: `Code: ${s.join_code}` })),
            el('div', { class: 'item-badges' }, statusBadge(s.status),
                el('span', { class: 'role-tag', text: s.is_gm ? 'GM' : 'Player' })),
            copyButton(s.join_code, 'Copy code'),
            el('button', {
                class: 'secondary-btn danger-btn small-btn',
                text: s.is_gm ? 'Delete' : 'Leave',
                attrs: { type: 'button', 'aria-label': `${s.is_gm ? 'Delete' : 'Leave'} session ${s.name}` },
                on: { click: () => removeSession(s) }
            })));
    });
    container.appendChild(list);
}

function loadSessions() {
    return loadInto(listContainer, () => apiFetch('/sessions/mine'), renderSessions, 'Loading your sessions...');
}

async function init() {
    const user = await requireUser();
    if (!user) return;
    currentUserId = user.id;

    document.getElementById('logoutBtn').addEventListener('click', logout);

    document.getElementById('joinForm').addEventListener('submit', async e => {
        e.preventDefault();
        hideAlert();
        const code = document.getElementById('joinCode').value.trim().toUpperCase();
        await withBusy(document.getElementById('joinSubmit'), 'Joining...', async () => {
            try {
                const data = await apiFetch('/sessions/join', { method: 'POST', body: JSON.stringify({ join_code: code }) });
                window.location.href = `lobby.html?id=${encodeURIComponent(data.session_id)}`;
            } catch (err) {
                showAlert(err.message);
            }
        });
    });

    document.getElementById('createForm').addEventListener('submit', async e => {
        e.preventDefault();
        hideAlert();
        const name = document.getElementById('sessionName').value.trim();
        await withBusy(document.getElementById('createSubmit'), 'Creating...', async () => {
            try {
                const data = await apiFetch('/sessions', { method: 'POST', body: JSON.stringify({ name, auto_relationships: true }) });
                window.location.href = `lobby.html?id=${encodeURIComponent(data.id)}`;
            } catch (err) {
                showAlert(err.message);
            }
        });
    });

    await loadSessions();
}

init();
