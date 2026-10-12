import { login, signup, checkAuth } from '../auth.js';
import { showAlert, hideAlert } from '../dom.js';
import { withBusy } from '../ui.js';

const loginPanel = document.getElementById('loginPanel');
const signupPanel = document.getElementById('signupPanel');

// Redirect if already logged in
if (localStorage.getItem('token')) {
    checkAuth().then(user => {
        if (user) window.location.href = 'dashboard.html';
    });
}

function show(panel, hidePanel, focusId) {
    hidePanel.hidden = true;
    panel.hidden = false;
    hideAlert();
    document.getElementById(focusId).focus();
}

document.getElementById('showSignup').addEventListener('click', e => {
    e.preventDefault();
    show(signupPanel, loginPanel, 'signupUsername');
});

document.getElementById('showLogin').addEventListener('click', e => {
    e.preventDefault();
    show(loginPanel, signupPanel, 'loginUsername');
});

function bindForm(formId, buttonId, busyLabel, action) {
    document.getElementById(formId).addEventListener('submit', async e => {
        e.preventDefault();
        hideAlert();
        // Inputs are left untouched on failure so nothing has to be retyped
        await withBusy(document.getElementById(buttonId), busyLabel, async () => {
            try {
                await action();
                window.location.href = 'dashboard.html';
            } catch (err) {
                showAlert(err.message);
            }
        });
    });
}

bindForm('loginForm', 'loginSubmit', 'Authenticating...', () =>
    login(document.getElementById('loginUsername').value, document.getElementById('loginPassword').value));

bindForm('signupForm', 'signupSubmit', 'Registering...', () =>
    signup(document.getElementById('signupUsername').value, document.getElementById('signupPassword').value));
