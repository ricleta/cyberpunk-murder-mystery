// DOM helpers. Everything that puts dynamic data on the page goes through here: text is
// always assigned with textContent / setAttribute, never parsed as HTML, so a player
// carrying an img tag with an error handler in their name is just a funny name.

const URL_ATTRS = new Set(['href', 'src', 'action', 'formaction', 'xlink:href']);

function isSafeUrl(value) {
    const normalized = String(value).replace(/[\u0000- ]/g, '').toLowerCase();
    return !/^(javascript|data|vbscript):/.test(normalized);
}

function appendChild(parent, child) {
    if (child === null || child === undefined || child === false) return;
    if (Array.isArray(child)) {
        child.forEach(c => appendChild(parent, c));
    } else if (child instanceof Node) {
        parent.appendChild(child);
    } else {
        parent.appendChild(document.createTextNode(String(child)));
    }
}

/**
 * el('button', { class: 'x', text: 'Hi', attrs: { type: 'button' }, dataset: { id: 3 }, on: { click: fn } }, ...children)
 * children may be strings, numbers, nodes, arrays or null/false (skipped).
 */
export function el(tag, options = {}, ...children) {
    const node = document.createElement(tag);
    const { class: className, text, attrs, dataset, on } = options;

    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);

    if (attrs) {
        for (const [name, value] of Object.entries(attrs)) {
            if (value === null || value === undefined || value === false) continue;
            if (/^on/i.test(name)) {
                throw new Error(`el(): refusing to set inline handler attribute "${name}"`);
            }
            if (URL_ATTRS.has(name.toLowerCase()) && !isSafeUrl(value)) {
                throw new Error(`el(): refusing unsafe URL for "${name}"`);
            }
            node.setAttribute(name, value === true ? '' : String(value));
        }
    }

    if (dataset) {
        for (const [key, value] of Object.entries(dataset)) {
            node.dataset[key] = String(value);
        }
    }

    if (on) {
        for (const [eventName, handler] of Object.entries(on)) {
            node.addEventListener(eventName, handler);
        }
    }

    children.forEach(child => appendChild(node, child));
    return node;
}

export function clear(node) {
    node.replaceChildren();
    return node;
}

export function setText(id, text) {
    const node = document.getElementById(id);
    if (node) node.textContent = text === null || text === undefined ? '' : String(text);
    return node;
}

// --- Alerts -------------------------------------------------------------------------------
// The page has <div id="alertBox" aria-live="polite"></div>. Each message is rendered as a
// child with role="alert" (errors) or role="status" (info / success), so screen readers
// announce it without the page having to move focus.

let hideTimer = null;

export function showAlert(message, type = 'error', containerId = 'alertBox') {
    const box = document.getElementById(containerId);
    if (!box) return;
    clearTimeout(hideTimer);
    box.replaceChildren(el('div', {
        class: `alert alert-${type}`,
        attrs: { role: type === 'error' ? 'alert' : 'status' },
        text: message
    }));
    if (type !== 'error') {
        hideTimer = setTimeout(() => hideAlert(containerId), 6000);
    }
}

export function hideAlert(containerId = 'alertBox') {
    clearTimeout(hideTimer);
    const box = document.getElementById(containerId);
    if (box) box.replaceChildren();
}
