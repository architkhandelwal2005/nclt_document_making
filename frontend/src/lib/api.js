import axios from "axios";

export const API_BASE = `${process.env.REACT_APP_BACKEND_URL || "http://127.0.0.1:8001"}/api`;
export const TOKEN_KEY = "casefile.token";

export const api = axios.create({ baseURL: API_BASE });

api.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) {
    config.headers = config.headers || {};
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  response => response,
  error => {
    if (error.response?.status === 401 && localStorage.getItem(TOKEN_KEY)) {
      localStorage.removeItem(TOKEN_KEY);
      window.dispatchEvent(new Event("casefile:unauthorized"));
    }
    return Promise.reject(error);
  },
);

export function errorMessage(error, fallback = "The operation could not be completed.") {
  const detail = error?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) return detail.message || fallback;
  if (Array.isArray(detail)) return detail.map(item => item.msg || String(item)).join(" ");
  return fallback;
}
