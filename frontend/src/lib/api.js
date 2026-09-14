import axios from "axios";

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000",
  timeout: 120000,
  headers: {
    "Content-Type": "application/json"
  }
});

// Repository and pull request reviews make one LLM call per file, and Groq's
// free tier throttles those calls, so allow more time than a snippet needs.
const MULTI_FILE_TIMEOUT_MS = 300000;

export async function runReview(payload) {
  const config = payload.repo_url ? { timeout: MULTI_FILE_TIMEOUT_MS } : undefined;
  const response = await api.post("/api/v1/review", payload, config);
  return response.data;
}

export async function runPullRequestReview(payload) {
  const response = await api.post("/api/v1/review/pr", payload, { timeout: MULTI_FILE_TIMEOUT_MS });
  return response.data;
}
