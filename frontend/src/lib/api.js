import axios from "axios";

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000",
  timeout: 120000,
  headers: {
    "Content-Type": "application/json"
  }
});

export async function runReview(payload) {
  const response = await api.post("/api/v1/review", payload);
  return response.data;
}

export async function runPullRequestReview(payload) {
  // Pull requests can touch many files, each needing its own LLM call.
  const response = await api.post("/api/v1/review/pr", payload, { timeout: 300000 });
  return response.data;
}
