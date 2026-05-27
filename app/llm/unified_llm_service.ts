// /* eslint-disable no-console */
// /**
//  * Production-ready, model-agnostic LLM service for code review.
//  * Node.js 18+ (native fetch).
//  */
// declare const process: any;
// export type Quality = "Basic" | "Good" | "Excellent";

// export interface ReviewResult {
//   score: number;
//   quality: Quality;
//   summary: string;
//   bugs: string[];
//   runtime_risks: string[];
//   code_issues: string[];
//   suggestions: string[];
//   fixes: Array<{ description: string; code: string }>;
// }

// type PrimaryProvider = "groq" | "openai";

// interface ServiceConfig {
//   primaryProvider: PrimaryProvider;
//   maxRetries: number; // retry count (2 => total 3 attempts)
//   retryDelayMs: number;
//   timeoutMs: number;
//   groqApiKey?: string;
//   groqModel: string;
//   openaiApiKey?: string;
//   openaiModel: string;
//   geminiApiKey?: string;
//   geminiModel: string;
//   openRouterApiKey?: string;
//   openRouterModel: string;
// }

// const CONFIG: ServiceConfig = {
//   primaryProvider: (process.env.PRIMARY_LLM_PROVIDER as PrimaryProvider) || "groq",
//   maxRetries: Number(process.env.LLM_MAX_RETRIES || 2),
//   retryDelayMs: Number(process.env.LLM_RETRY_DELAY_MS || 700),
//   timeoutMs: Number(process.env.LLM_TIMEOUT_MS || 10000),
//   groqApiKey: process.env.GROQ_API_KEY,
//   groqModel: process.env.GROQ_MODEL || "llama-3.1-8b-instant",
//   openaiApiKey: process.env.OPENAI_API_KEY,
//   openaiModel: process.env.OPENAI_MODEL || "gpt-4o-mini",
//   geminiApiKey: process.env.GEMINI_API_KEY,
//   geminiModel: process.env.GEMINI_MODEL || "gemini-1.5-flash",
//   openRouterApiKey: process.env.OPENROUTER_API_KEY,
//   openRouterModel: process.env.OPENROUTER_MODEL || "meta-llama/llama-3.1-8b-instruct",
// };

// export const REVIEW_PROMPT = `
// You are a strict and intelligent code review assistant.

// Analyze the given code and return STRICT JSON only.

// IMPORTANT RULES:

// 1. Do NOT invent issues.
// 2. Only report REAL problems.
// 3. If there is ANY issue, you MUST provide a valid fix.
// 4. Each fix MUST include:
//    - description (clear explanation)
//    - full corrected code (complete working function/code)
// 5. Do NOT return empty or vague fixes.
// 6. If no issues exist, fixes MUST be an empty array.

// STRICT OUTPUT FORMAT:

// {
//   "score": number,
//   "quality": "Basic" | "Good" | "Excellent",
//   "summary": "string",
//   "bugs": [],
//   "runtime_risks": [],
//   "code_issues": [],
//   "suggestions": [],
//   "fixes": [
//     {
//       "description": "string",
//       "code": "string"
//     }
//   ]
// }
// `;

// const FINAL_FALLBACK: ReviewResult = {
//   score: 0,
//   quality: "Basic",
//   summary: "All models are busy. Please try again.",
//   bugs: [],
//   runtime_risks: [],
//   code_issues: [],
//   suggestions: [],
//   fixes: [],
// };

// function sleep(ms: number): Promise<void> {
//   return new Promise((resolve) => setTimeout(resolve, ms));
// }

// function clampScore(input: unknown): number {
//   const value = Number(input);
//   if (!Number.isFinite(value)) return 0;
//   return Math.max(0, Math.min(10, value));
// }

// function normalizeQuality(input: unknown): Quality {
//   const value = String(input ?? "").trim().toLowerCase();
//   if (value === "excellent") return "Excellent";
//   if (value === "good") return "Good";
//   return "Basic";
// }

// function computeScore(result: {
//   bugs: string[];
//   runtime_risks: string[];
//   code_issues: string[];
//   suggestions: string[];
// }): number {
//   let score = 10;

//   score -= result.bugs.length * 3;
//   score -= result.runtime_risks.length * 2;
//   score -= result.code_issues.length * 1;

//   // suggestions are soft → no penalty

//   return Math.max(0, Math.min(10, score));
// }

// function computeQuality(score: number): "Basic" | "Good" | "Excellent" {
//   if (score >= 9) return "Excellent";
//   if (score >= 6) return "Good";
//   return "Basic";
// }

// function normalizeList(input: unknown): string[] {
//   if (!Array.isArray(input)) return [];
//   const seen = new Set<string>();
//   const out: string[] = [];
//   for (const item of input) {
//     const text = String(item ?? "").trim();
//     if (!text) continue;
//     const key = text.toLowerCase();
//     if (seen.has(key)) continue;
//     seen.add(key);
//     out.push(text);
//   }
//   return out;
// }

// function normalizeIssues(input: unknown): string[] {
//   if (!Array.isArray(input)) return [];

//   const seen = new Set<string>();
//   const out: string[] = [];

//   for (const item of input) {
//     const text = String(item ?? "").trim();
//     if (!text) continue;

//     const lower = text.toLowerCase();

//     // ❌ remove vague / useless issues
//     if (lower.includes("consider")) continue;
//     if (lower.includes("might")) continue;
//     if (lower.includes("could")) continue;

//     if (seen.has(lower)) continue;
//     seen.add(lower);
//     out.push(text);
//   }

//   return out;
// }

// function normalizeResult(raw: unknown): ReviewResult | null {
//   if (!raw || typeof raw !== "object") return null;
//   const obj = raw as Record<string, unknown>;

//   const result: ReviewResult = {
//     score: 0,
//     quality: "Basic",
//     summary: String(obj.summary ?? "").trim(),
//     bugs: normalizeIssues(obj.bugs),
//     runtime_risks: normalizeIssues(obj.runtime_risks),
//     code_issues: normalizeIssues(obj.code_issues),
//     suggestions: normalizeList(obj.suggestions),
//     fixes: [],
//   };

//   const rawFixes = Array.isArray(obj.fixes) ? obj.fixes : [];

//   const validFixes = rawFixes
//     .map((fix) => {
//       if (!fix || typeof fix !== "object") return null;

//       let desc = String((fix as any).description ?? "").trim();
//       const code = String((fix as any).code ?? "").trim();

//       if (!code || code.length < 20) return null;

//       // ❌ remove fake descriptions
//       const lower = desc.toLowerCase();
//       if (
//         lower.includes("analyzer returned") ||
//         lower.includes("no details") ||
//         lower.includes("no fix")
//       ) {
//         desc = "";
//       }

//       // ✅ AUTO-GENERATE DESCRIPTION (KEY FIX)
//       if (!desc) {
//         desc = "Fix applied to resolve detected issue";
//       }

//       return {
//         description: desc,
//         code,
//       };
//     })
//     .filter(Boolean);

//   const hasIssues =
//     result.bugs.length > 0 ||
//     result.runtime_risks.length > 0 ||
//     result.code_issues.length > 0;

//   // STRICT TYPE-SAFE FILTER
//   let finalFixes: { description: string; code: string }[] = validFixes.filter(
//     (f): f is { description: string; code: string } =>
//       !!f &&
//       typeof (f as any).description === "string" &&
//       typeof (f as any).code === "string" &&
//       (f as any).description.trim().length > 0 &&
//       (f as any).code.trim().length > 0
//   );

//   // STRICT RULE: no issues → no fixes
//   if (!hasIssues) {
//     finalFixes = [];
//   }

//   result.fixes = finalFixes;

//   // FINAL STEP: ignore model score/quality, compute deterministically.
//   result.score = computeScore(result);
//   result.quality = computeQuality(result.score);

//   return result;
// }

// function buildPrompt(code: string): string {
//   return `${REVIEW_PROMPT}\n\nCODE:\n${code.slice(0, 25000)}`;
// }

// /**
//  * Extract the first balanced JSON object from raw model text.
//  */
// function extractJson(raw: string): string | null {
//   if (!raw) return null;
//   const cleaned = raw.replace(/```json/gi, "```").replace(/```/g, "").trim();
//   const start = cleaned.indexOf("{");
//   if (start < 0) return null;

//   let depth = 0;
//   let inString = false;
//   let escaped = false;

//   for (let i = start; i < cleaned.length; i += 1) {
//     const ch = cleaned[i];
//     if (inString) {
//       if (escaped) escaped = false;
//       else if (ch === "\\") escaped = true;
//       else if (ch === '"') inString = false;
//       continue;
//     }
//     if (ch === '"') {
//       inString = true;
//       continue;
//     }
//     if (ch === "{") depth += 1;
//     if (ch === "}") {
//       depth -= 1;
//       if (depth === 0) return cleaned.slice(start, i + 1).trim();
//     }
//   }
//   return null;
// }

// /**
//  * Robust JSON parse with tolerance for extra text around JSON payload.
//  */
// export function parseJSON(rawText: string): ReviewResult | null {
//   const trimmed = String(rawText || "").trim();
//   if (!trimmed) return null;

//   // If provider returned full API envelope JSON, extract content and parse again.
//   try {
//     const maybeEnvelope = JSON.parse(trimmed) as any;
//     const chatContent = maybeEnvelope?.choices?.[0]?.message?.content;
//     if (typeof chatContent === "string") {
//       return parseJSON(chatContent);
//     }
//     const geminiContent = maybeEnvelope?.candidates?.[0]?.content?.parts?.[0]?.text;
//     if (typeof geminiContent === "string") {
//       return parseJSON(geminiContent);
//     }
//   } catch {
//     // not a direct envelope, continue with generic extraction
//   }

//   const jsonText = extractJson(rawText);
//   if (!jsonText) return null;
//   try {
//     const parsed = JSON.parse(jsonText);
//     return normalizeResult(parsed);
//   } catch {
//     return null;
//   }
// }

// async function fetchWithTimeout(url: string, init: RequestInit, timeoutMs: number): Promise<Response> {
//   const controller = new AbortController();
//   const id = setTimeout(() => controller.abort(), timeoutMs);
//   try {
//     return await fetch(url, { ...init, signal: controller.signal });
//   } finally {
//     clearTimeout(id);
//   }
// }

// /**
//  * IMPORTANT wrapper:
//  * Detect capacity failures even when API technically returns success text.
//  */
// export async function safeCall(fn: () => Promise<string>): Promise<string> {
//   for (let i = 0; i < 3; i += 1) {
//     try {
//       const res = await fn();
//       if (!res) throw new Error("Empty response");
//       if (typeof res === "string" && res.toLowerCase().includes("at capacity")) {
//         throw new Error("Model at capacity");
//       }
//       return res;
//     } catch (err) {
//       if (i === 2) throw err;
//       await new Promise((resolve) => setTimeout(resolve, 1000));
//     }
//   }
//   throw new Error("Provider call failed");
// }

// async function callOpenAI(prompt: string, cfg: ServiceConfig = CONFIG): Promise<string> {
//   if (!cfg.openaiApiKey) throw new Error("Missing OPENAI_API_KEY");
//   const res = await fetchWithTimeout(
//     "https://api.openai.com/v1/chat/completions",
//     {
//       method: "POST",
//       headers: {
//         Authorization: `Bearer ${cfg.openaiApiKey}`,
//         "Content-Type": "application/json",
//       },
//       body: JSON.stringify({
//         model: cfg.openaiModel,
//         temperature: 0.2,
//         messages: [{ role: "user", content: prompt }],
//       }),
//     },
//     cfg.timeoutMs
//   );
//   if (!res.ok) throw new Error(`OpenAI request failed: ${res.status}`);
//   const data = (await res.json()) as any;
//   return String(data?.choices?.[0]?.message?.content ?? "");
// }

// /**
//  * Primary provider call.
//  * Uses Groq by default, or OpenAI when PRIMARY_LLM_PROVIDER=openai.
//  */
// export async function callGroq(prompt: string, cfg: ServiceConfig = CONFIG): Promise<string> {
//   if (cfg.primaryProvider === "openai") {
//     return callOpenAI(prompt, cfg);
//   }
//   if (!cfg.groqApiKey) throw new Error("Missing GROQ_API_KEY");
//   const res = await fetchWithTimeout(
//     "https://api.groq.com/openai/v1/chat/completions",
//     {
//       method: "POST",
//       headers: {
//         Authorization: `Bearer ${cfg.groqApiKey}`,
//         "Content-Type": "application/json",
//       },
//       body: JSON.stringify({
//         model: cfg.groqModel,
//         temperature: 0.2,
//         messages: [{ role: "user", content: prompt }],
//       }),
//     },
//     cfg.timeoutMs
//   );
//   const text = await res.text();
//   if (!res.ok || text.toLowerCase().includes("capacity") || text.toLowerCase().includes("error")) {
//     throw new Error("Groq failed");
//   }
//   return text;
// }

// /**
//  * Gemini fallback call.
//  * Required endpoint:
//  * https://generativelanguage.googleapis.com/v1/models/gemini-1.5-flash:generateContent
//  */
// export async function callGemini(prompt: string, cfg: ServiceConfig = CONFIG): Promise<string> {
//   if (!cfg.geminiApiKey) throw new Error("Missing GEMINI_API_KEY");
//   const model = cfg.geminiModel || "gemini-1.5-flash";
//   const endpoint = `https://generativelanguage.googleapis.com/v1/models/${model}:generateContent?key=${cfg.geminiApiKey}`;
//   const res = await fetchWithTimeout(
//     endpoint,
//     {
//       method: "POST",
//       headers: { "Content-Type": "application/json" },
//       body: JSON.stringify({
//         generationConfig: { temperature: 0.2 },
//         contents: [{ parts: [{ text: prompt }] }],
//       }),
//     },
//     cfg.timeoutMs
//   );
//   if (!res.ok) {
//     throw new Error("Gemini failed");
//   }
//   const data = (await res.json()) as any;
//   return String(data?.candidates?.[0]?.content?.parts?.[0]?.text ?? "");
// }

// export async function callOpenRouter(prompt: string, cfg: ServiceConfig = CONFIG): Promise<string> {
//   if (!cfg.openRouterApiKey) throw new Error("Missing OPENROUTER_API_KEY");
//   const res = await fetchWithTimeout(
//     "https://openrouter.ai/api/v1/chat/completions",
//     {
//       method: "POST",
//       headers: {
//         Authorization: `Bearer ${cfg.openRouterApiKey}`,
//         "Content-Type": "application/json",
//       },
//       body: JSON.stringify({
//         model: cfg.openRouterModel,
//         temperature: 0.2,
//         messages: [{ role: "user", content: prompt }],
//       }),
//     },
//     cfg.timeoutMs
//   );
//   if (!res.ok) throw new Error(`OpenRouter request failed: ${res.status}`);
//   const data = (await res.json()) as any;
//   return String(data?.choices?.[0]?.message?.content ?? "");
// }

// async function callPrimaryProvider(prompt: string): Promise<string> {
//   return safeCall(() => callGroq(prompt, CONFIG));
// }

// function safeParseJSON(rawText: string): any | null {
//   const jsonText = extractJson(String(rawText ?? ""));
//   if (!jsonText) return null;
//   try {
//     return JSON.parse(jsonText);
//   } catch {
//     return null;
//   }
// }

// async function ensureFixesWhenIssues(result: ReviewResult, code: string): Promise<ReviewResult> {
//   const hasIssues =
//     result.bugs.length > 0 || result.runtime_risks.length > 0 || result.code_issues.length > 0;

//   let finalFixes = Array.isArray(result.fixes) ? result.fixes : [];

//   if (hasIssues && finalFixes.length === 0) {
//     try {
//       const fixPrompt = `
// You are a strict code fixer.

// Given the issue and code, return ONLY a valid fix.

// RULES:
// - Must return JSON
// - Must include:
//   {
//     "description": "...",
//     "code": "FULL corrected code"
//   }
// - No explanation outside JSON

// CODE:
// ${code}

// ISSUES:
// ${JSON.stringify([
//   ...result.bugs,
//   ...result.runtime_risks,
//   ...result.code_issues
// ])}
// `;

//       const retryResponse = await callPrimaryProvider(fixPrompt);
//       const retryJson = safeParseJSON(retryResponse);

//       if (
//         retryJson &&
//         retryJson.description &&
//         retryJson.code
//       ) {
//         finalFixes = [
//           {
//             description: retryJson.description,
//             code: retryJson.code
//           }
//         ];
//       }
//     } catch (e) {
//       // fail silently (strict mode)
//     }}

//   result.fixes = finalFixes;
//   return result;
// }

// /**
//  * Unified entrypoint.
//  * - Same prompt across all providers
//  * - Retries each provider up to 2 times
//  * - Fallback order: primary -> Gemini -> OpenRouter (optional) -> final safe object
//  */
// export async function analyzeCode(code: string): Promise<ReviewResult> {
//   const prompt = buildPrompt(code);

//   // 1) Primary: Groq/OpenAI
//   try {
//     const res = await safeCall(() => callGroq(prompt, CONFIG));
//     const parsed = parseJSON(res);
//     if (parsed) return await ensureFixesWhenIssues(parsed, code);
//     throw new Error("Invalid JSON from primary provider");
//   } catch {
//     console.log("⚠️ Groq failed → Gemini fallback");
//   }

//   // 2) Gemini fallback
//   try {
//     const res = await safeCall(() => callGemini(prompt, CONFIG));
//     const parsed = parseJSON(res);
//     if (parsed) return await ensureFixesWhenIssues(parsed, code);
//     throw new Error("Invalid JSON from Gemini");
//   } catch {
//     // continue to optional fallback
//   }

//   // 3) Optional OpenRouter fallback
//   if (CONFIG.openRouterApiKey) {
//     console.log("⚠️ Falling back to OpenRouter...");
//     try {
//       const res = await safeCall(() => callOpenRouter(prompt, CONFIG));
//       const parsed = parseJSON(res);
//       if (parsed) return await ensureFixesWhenIssues(parsed, code);
//     } catch {
//       // final fallback next
//     }
//   }

//   console.log("⚠️ All models failed");
//   return FINAL_FALLBACK;
// }

// export default analyzeCode;

/* eslint-disable no-console */
declare const process: any;

export type Quality = "Basic" | "Good" | "Excellent";

export interface ReviewResult {
  score: number;
  quality: Quality;
  summary: string;
  bugs: string[];
  runtime_risks: string[];
  code_issues: string[];
  suggestions: string[];
  fixes: Array<{ description: string; code: string }>;
   llm_score?: number;
}

type PrimaryProvider = "groq" | "openai";

interface ServiceConfig {
  primaryProvider: PrimaryProvider;
  maxRetries: number;
  retryDelayMs: number;
  timeoutMs: number;
  groqApiKey?: string;
  groqModel: string;
  openaiApiKey?: string;
  openaiModel: string;
  geminiApiKey?: string;
  geminiModel: string;
  openRouterApiKey?: string;
  openRouterModel: string;
}

const CONFIG: ServiceConfig = {
  primaryProvider: (process.env.PRIMARY_LLM_PROVIDER as PrimaryProvider) || "groq",
  maxRetries: Number(process.env.LLM_MAX_RETRIES || 2),
  retryDelayMs: Number(process.env.LLM_RETRY_DELAY_MS || 700),
  timeoutMs: Number(process.env.LLM_TIMEOUT_MS || 10000),
  groqApiKey: process.env.GROQ_API_KEY,
  groqModel: process.env.GROQ_MODEL || "llama-3.1-8b-instant",
  openaiApiKey: process.env.OPENAI_API_KEY,
  openaiModel: process.env.OPENAI_MODEL || "gpt-4o-mini",
  geminiApiKey: process.env.GEMINI_API_KEY,
  geminiModel: process.env.GEMINI_MODEL || "gemini-1.5-flash",
  openRouterApiKey: process.env.OPENROUTER_API_KEY,
  openRouterModel: process.env.OPENROUTER_MODEL || "meta-llama/llama-3.1-8b-instruct",
};

export const REVIEW_PROMPT = `
You are a strict and intelligent code review assistant.

Analyze the given code and return STRICT JSON only.

IMPORTANT RULES:
1. Do NOT invent issues.
2. Only report REAL problems.
3. If there is ANY issue, you MUST provide a valid fix.
4. Each fix MUST include:
   - description
   - full corrected code
5. Do NOT return empty or vague fixes.
6. If no issues exist, fixes MUST be an empty array.

STRICT OUTPUT FORMAT:
{
  "score": number,
  "quality": "Basic" | "Good" | "Excellent",
  "summary": "string",
  "bugs": [],
  "runtime_risks": [],
  "code_issues": [],
  "suggestions": [],
  "fixes": [
    {
      "description": "string",
      "code": "string"
    }
  ]
}
`;

const FINAL_FALLBACK: ReviewResult = {
  score: 0,
  quality: "Basic",
  summary: "All models are busy. Please try again.",
  bugs: [],
  runtime_risks: [],
  code_issues: [],
  suggestions: [],
  fixes: [],
};

function computeScore(result: {
  bugs: string[];
  runtime_risks: string[];
  code_issues: string[];
  llm_score?: number;
}): number {
   let staticScore = 10;

  staticScore -= result.bugs.length * 2;
  staticScore -= result.runtime_risks.length * 1.5;
  staticScore -= result.code_issues.length * 1;

  staticScore = Math.max(0, Math.min(10, staticScore));

  // ✅ combine with LLM score
  if (result.llm_score !== undefined) {
    return (staticScore * 0.6 + result.llm_score * 0.4);
  }

  return staticScore;
}

function computeQuality(score: number): Quality {
  if (score >= 9) return "Excellent";
  if (score >= 6) return "Good";
  return "Basic";
}

function normalizeList(input: unknown): string[] {
  if (!Array.isArray(input)) return [];
   return input
    .map((x: any) => {
      if (typeof x === "string") return x;
      if (typeof x === "object" && x?.description) return x.description;
      return "";
    })
    .filter(Boolean);
}

function normalizeResult(raw: any): ReviewResult {
  return {
    score: 0,
    quality: "Basic",
    llm_score: Number(raw.score?.final_score ?? raw.score ?? 0),
    summary: String(raw.summary || ""),
    bugs: normalizeList(raw.bugs),
    runtime_risks: normalizeList(raw.runtime_risks),
    code_issues: normalizeList(raw.code_issues),
    suggestions: normalizeList(raw.suggestions),
fixes: Array.isArray(raw.fixes)
  ? raw.fixes.filter(
      (f: any) =>
        f &&
        typeof f.description === "string" &&
        f.description.trim().length > 5 &&
        typeof f.code === "string" &&
        f.code.trim().length > 5
    )
  : [],
  };
}

function buildPrompt(code: string): string {
  return `${REVIEW_PROMPT}\n\nCODE:\n${code}`;
}

function extractJson(text: string): string | null {
  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start === -1 || end === -1) return null;
  return text.slice(start, end + 1);
}

function parseJSON(raw: string): ReviewResult | null {
  try {
    const json = extractJson(raw);
    if (!json) return null;
    return normalizeResult(JSON.parse(json));
  } catch {
    return null;
  }
}

/* ✅ NEW: deterministic rule engine */
function enforceBasicRules(code: string, result: ReviewResult): ReviewResult {
  const updated = { ...result };

  

  const hasIssues =
  updated.bugs.length > 0 ||
  updated.runtime_risks.length > 0 ||
  updated.code_issues.length > 0;

// ❗ Case 1: No issues → no fixes
if (!hasIssues) {
  updated.fixes = [];
}

// ❗ Case 2: Issues exist but NO valid fixes → add safe fallback
else if (hasIssues && updated.fixes.length === 0) {
  updated.fixes = updated.runtime_risks.length > 0
    ? [{
        description: "Add proper runtime validation to handle edge cases",
        code: "// Add validation or error handling (e.g., check inputs before operations)"
      }]
    : updated.code_issues.length > 0
    ? [{
        description: "Fix logical or syntax issues in the code",
        code: "// Correct the logic or syntax based on the issue"
      }]
    : updated.bugs.length > 0
    ? [{
        description: "Fix detected bugs in the code",
        code: "// Resolve the bug causing incorrect behavior"
      }]
    : [];
}

  updated.score = computeScore(updated);
  updated.quality = computeQuality(updated.score);

  return updated;
}

async function callLLM(prompt: string): Promise<string> {
  const res = await fetch("https://api.groq.com/openai/v1/chat/completions", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${CONFIG.groqApiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: CONFIG.groqModel,
      temperature: 0, // ✅ important
      messages: [{ role: "user", content: prompt }],
    }),
  });

  const data = await res.json();
  return data?.choices?.[0]?.message?.content || "";
}

export async function analyzeCode(code: string): Promise<ReviewResult> {
  try {
    const prompt = buildPrompt(code);
    const raw = await callLLM(prompt);
    const parsed = parseJSON(raw);

    if (!parsed) return FINAL_FALLBACK;

    return enforceBasicRules(code, parsed);
  } catch {
    return FINAL_FALLBACK;
  }
}

export default analyzeCode;