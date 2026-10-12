import { API_BASE } from './config.js';

// GitHub Pages project sites live under /repo-name/, so every redirect is relative
// (pages all sit next to each other) and "am I on the login page" looks at the file name only.
export function isLoginPage() {
    const last = window.location.pathname.split('/').pop();
    return last === '' || last === 'index.html';
}

export function redirectToLogin() {
    if (!isLoginPage()) {
        window.location.href = 'index.html';
    }
}

// Errors carry the HTTP status and the server's machine-readable code (e.g. 'session_locked').
export class ApiError extends Error {
    constructor(message, status, code) {
        super(message);
        this.name = 'ApiError';
        this.status = status;
        this.code = code || null;
    }
}

export async function apiFetch(endpoint, options = {}) {
    const token = localStorage.getItem('token');

    const headers = {
        'Content-Type': 'application/json',
        ...options.headers
    };

    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }

    let response;
    try {
        response = await fetch(`${API_BASE}${endpoint}`, { ...options, headers });
    } catch (networkError) {
        throw new ApiError('Could not reach the server. Check your connection and try again.', 0);
    }

    if (response.status === 401 && token) {
        // Token expired or invalid: forget it and go back to the login page
        localStorage.removeItem('token');
        localStorage.removeItem('user');
        redirectToLogin();
    }

    let data = null;
    if (response.headers.get('content-type')?.includes('application/json')) {
        data = await response.json();
    }

    if (!response.ok) {
        throw new ApiError(data?.error || 'An error occurred', response.status, data?.code);
    }

    return data;
}
