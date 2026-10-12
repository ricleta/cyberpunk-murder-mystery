// Local files: download a JSON backup, or pick one to restore from.

export const MAX_CHARACTER_FILE_BYTES = 64 * 1024;
export const MAX_BUNDLE_FILE_BYTES = 2 * 1024 * 1024;

/** ASCII [a-z0-9-] slug for file names; 'character' when nothing usable is left. */
export function slugify(name) {
    const slug = String(name || '')
        .normalize('NFKD').replace(/[̀-ͯ]/g, '')
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '')
        .slice(0, 60)
        .replace(/-+$/g, '');
    return slug || 'character';
}

export function downloadJson(filename, data) {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    link.style.display = 'none';
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Reads and parses one chosen file. Throws an Error with a message fit to show the user. */
export async function readJsonFile(file, { maxBytes = MAX_BUNDLE_FILE_BYTES } = {}) {
    if (file.size > maxBytes) {
        throw new Error(`That file is too large (${Math.round(file.size / 1024)} KB; the limit is ${Math.round(maxBytes / 1024)} KB).`);
    }
    let text;
    try {
        text = await file.text();
    } catch (e) {
        throw new Error('Could not read that file.');
    }
    try {
        return JSON.parse(text);
    } catch (e) {
        throw new Error('That file is not valid JSON. Choose a character file exported from this app.');
    }
}

/**
 * Opens the browser's file picker (must be called from a click handler). Resolves with the
 * parsed JSON, or null when the user cancels. Rejects with a friendly Error.
 */
export function pickJsonFile({ maxBytes = MAX_BUNDLE_FILE_BYTES } = {}) {
    return new Promise((resolve, reject) => {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = '.json,application/json';
        input.style.display = 'none';
        document.body.appendChild(input);
        const done = () => input.remove();
        input.addEventListener('change', async () => {
            const file = input.files && input.files[0];
            done();
            if (!file) { resolve(null); return; }
            try {
                resolve(await readJsonFile(file, { maxBytes }));
            } catch (err) {
                reject(err);
            }
        });
        input.addEventListener('cancel', () => { done(); resolve(null); });
        input.click();
    });
}
