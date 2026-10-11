import { apiFetch } from './api.js';

export async function login(username, password) {
    const data = await apiFetch('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password })
    });
    localStorage.setItem('token', data.token);
    localStorage.setItem('user', JSON.stringify(data.user));
    return data;
}

export async function signup(username, password) {
    const data = await apiFetch('/auth/signup', {
        method: 'POST',
        body: JSON.stringify({ username, password })
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
