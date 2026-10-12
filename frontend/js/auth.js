import { apiFetch, redirectToLogin } from './api.js';

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
    window.location.href = 'index.html';
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

// Every page except the login page starts with this: returns the signed-in user or
// sends the visitor to the login page (and returns null).
export async function requireUser() {
    const user = await checkAuth();
    if (!user) {
        redirectToLogin();
        return null;
    }
    return user;
}
