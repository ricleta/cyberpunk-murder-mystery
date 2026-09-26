const API_BASE = `http://${window.location.hostname}:5000/api`;

export async function apiFetch(endpoint, options = {}) {
    const token = localStorage.getItem('token');
    
    const headers = {
        'Content-Type': 'application/json',
        ...options.headers
    };

    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }

    const config = {
        ...options,
        headers
    };

    const response = await fetch(`${API_BASE}${endpoint}`, config);
    
    if (response.status === 401) {
        // Token expired or invalid, handle logout
        localStorage.removeItem('token');
        localStorage.removeItem('user');
        if (window.location.pathname !== '/index.html' && window.location.pathname !== '/') {
            window.location.href = '/index.html';
        }
    }

    let data = null;
    if (response.headers.get("content-type")?.includes("application/json")) {
        data = await response.json();
    }
    
    if (!response.ok) {
        throw new Error(data?.error || 'An error occurred');
    }

    return data;
}
