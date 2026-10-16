// Where the backend API lives.
//
// DEPLOYING: edit PRODUCTION_API_BASE below to point at your PythonAnywhere app before
// publishing the frontend (see backend/DEPLOY.md). It is used on every host except
// localhost / 127.0.0.1, where the local Flask dev server on port 5000 is used.
export const PRODUCTION_API_BASE = 'https://ricleta.pythonanywhere.com/api';

const host = window.location.hostname;
const isLocal = host === 'localhost' || host === '127.0.0.1';

export const API_BASE = isLocal ? `http://${host}:5000/api` : PRODUCTION_API_BASE;
