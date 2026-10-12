// Rendering pieces shared by the character page and the GM's "print all sheets" page.
// Everything is built with el(), so names and generated text are always plain text.
import { el } from './dom.js';
import { badge, labelize, pointsBadge } from './ui.js';

export const KIND_LABELS = { victim_tie: 'Tie to the victim', self: 'Secret', lie: 'Lie', observation: 'Observation' };

export function secretItem(secret) {
    const badges = el('div', { class: 'item-badges' },
        badge(KIND_LABELS[secret.kind] || labelize(secret.kind), secret.kind === 'lie' ? 'warn' : 'info'),
        // Severity of someone else's secret would single out the killer's trail secrets
        secret.about_self && secret.severity ? badge(`Severity ${secret.severity}`, secret.severity >= 3 ? 'danger' : 'neutral') : null);
    return el('div', { class: 'sheet-item' },
        el('div', { class: 'item-text' },
            secret.about_self ? null : el('div', { class: 'muted' }, 'About ', el('strong', { text: secret.subject_name || 'someone' })),
            el('div', { text: secret.text }),
            !secret.about_self && secret.how_learned ? el('div', { class: 'objective-meta', text: `How you learned it: ${secret.how_learned}` }) : null),
        badges);
}

export function relationshipItem({ other, type, text }) {
    return el('div', { class: 'sheet-item' },
        el('div', { class: 'item-text' },
            el('div', {}, el('strong', { text: other || 'Unknown' })),
            el('div', { text: text || '' })),
        el('div', { class: 'item-badges' }, badge(labelize(type), 'info')));
}

export function readOnlyObjective(o) {
    return el('div', { class: 'objective-row' }, pointsBadge(o.points), el('div', { class: 'objective-body', text: o.text }));
}

/** Visible only when printing. */
export function printHeader({ sessionName, characterName, role, date }) {
    return el('header', { class: 'print-only print-header' },
        el('p', { class: 'print-session', text: sessionName || '' }),
        el('h1', { text: characterName || 'Unnamed' }),
        el('p', { text: `${(role || '').toUpperCase()}  ·  Printed ${date}` }));
}

/** One block at the end of the sheet (not a running footer, which would repeat on every page). */
export function printFooter(characterName) {
    return el('footer', { class: 'print-only print-footer' },
        `Private — for ${characterName || 'this character'} only. Do not share your sheet with other players.`);
}

function section(title, ...children) {
    return el('section', { class: 'sheet-section' }, el('h2', { text: title }), children);
}

function emptyNote(text) {
    return el('p', { class: 'muted', text });
}

/**
 * One printable sheet from a /sheet payload. Built from that payload only, so nothing
 * GM-only can appear, whoever requested it.
 */
export function renderPrintSheet(payload, date = new Date().toLocaleDateString()) {
    const c = payload.character;
    const own = payload.secrets.own.map(s => ({ ...s, about_self: true }));
    const known = payload.secrets.known.map(s => ({ ...s, about_self: false }));

    return el('article', { class: 'print-sheet', dataset: { characterId: c.id } },
        el('header', { class: 'print-sheet-header' },
            el('p', { class: 'print-session', text: payload.session.name }),
            el('h2', { class: 'print-sheet-title', text: c.display_name || 'Unnamed' }),
            el('p', { text: `${(c.role_key || '').toUpperCase()}  ·  Printed ${date}` })),
        section('Description', c.description
            ? el('p', { class: 'print-pre', text: c.description }) : emptyNote('No description.')),
        section('Traits', c.traits.length
            ? el('p', { text: c.traits.join(', ') }) : emptyNote('No traits.')),
        section('Secrets & Lies',
            el('h3', { text: 'Your secrets' }),
            own.length ? own.map(secretItem) : emptyNote(payload.generated ? 'You have no secrets of your own.' : 'Not generated yet.'),
            el('h3', { text: 'What you know about others' }),
            known.length ? known.map(secretItem) : emptyNote(payload.generated ? 'You do not know anyone else’s secrets.' : 'Not generated yet.')),
        section('Relationships', payload.relationships.length
            ? payload.relationships.map(r => relationshipItem({ other: r.other_name, type: r.rel_type, text: r.text }))
            : emptyNote(payload.generated ? 'No relationships.' : 'Not generated yet.')),
        section('Objectives', payload.objectives.length
            ? payload.objectives.map(readOnlyObjective) : emptyNote(payload.generated ? 'No objectives.' : 'Not generated yet.')),
        el('footer', { class: 'print-sheet-footer',
            text: `Private — for ${c.display_name || 'this character'} only. Do not share your sheet with other players.` }));
}
