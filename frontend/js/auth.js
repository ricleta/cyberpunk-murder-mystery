import { apiFetch } from './api.js';

export async function login(email, password) {
    const data = await apiFetch('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ email, password })
    });
    localStorage.setItem('token', data.token);
    localStorage.setItem('user', JSON.stringify(data.user));
    return data;
}

export async function signup(email, password, display_name) {
    const data = await apiFetch('/auth/signup', {
        method: 'POST',
        body: JSON.stringify({ email, password, display_name })
    });
    localStorage.setItem('token', data.token);
    localStorage.setItem('user', JSON.stringify(data.user));
    return data;
}

export function logout() {
    localStorage.removeItem('token');
    localStorage.removeItem('user');
    window.location.href = '/index.html';
}

export async function checkAuth() {
    const token = localStorage.getItem('token');
    if (!token) {
        return null;
    }
    try {
        const data = await apiFetch('/auth/me');
        return data.user;
    } catch (e) {
        return null;
    }
}
