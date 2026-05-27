import { useEffect, useState } from "react";

import CollapsibleSection from "./CollapsibleSection";
import Spinner from "./Spinner";

function getScorePalette(score) {
  const value = Math.max(0, Math.min(10, Number(score) || 0));
  if (value <= 4) {
    return {
      ring: "#ef4444",
      text: "text-red-300",
      tone: "bg-red-500/10 border-red-500/30"
    };
  }
  if (value <= 7) {
    return {
      ring: "#eab308",
      text: "text-yellow-300",
      tone: "bg-yellow-500/10 border-yellow-500/30"
    };
  }
  return {
    ring: "#22c55e",
    text: "text-green-300",
    tone: "bg-green-500/10 border-green-500/30"
  };
}

function getScoreInterpretation(score) {
  const value = Math.max(0, Math.min(10, Number(score) || 0));
  if (value <= 4) {
    return "Poor";
  }
  if (value <= 7) {
    return "Average";
  }
  return "Good";
}

function normalizeText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function canonicalize(value) {
  return normalizeText(value).toLowerCase().replace(/[^a-z0-9\s]/g, "").replace(/\s+/g, " ").trim();
}

function extractFixCode(item) {
  if (!item || typeof item !== "object") {
    return "";
  }
  const codeValue =
    item.fix_code ||
    item.fixed_code ||
    item.replacement_code ||
    item.updated_code ||
    item.code ||
    item.snippet;
  return typeof codeValue === "string" ? codeValue.trim() : "";
}

function extractDescription(item) {
  if (typeof item === "string") {
    const text = normalizeText(item);
    return text || ""; // ❗ NO fallback
  }

  if (!item || typeof item !== "object") {
    return ""; // ❗ NO fallback
  }

  const text = normalizeText(
    item.description ||
    item.message ||
    item.title ||
    item.reason ||
    item.text ||
    item.details ||
    item.summary
  );

  return text || ""; // ❗ NO fallback
}

function extractItemType(item, fallbackType) {
  if (!item || typeof item !== "object") {
    return fallbackType;
  }
  const rawType = item.type || item.category || item.severity || item.level || item.kind || item.symbol || fallbackType;
  return normalizeText(rawType).toUpperCase() || fallbackType;
}

function hasFunctionParamTypeHints(code) {
  const source = String(code || "");
  return /def\s+[A-Za-z_]\w*\s*\([^)]*:\s*[^)]*\)\s*:/m.test(source);
}

function isTypeValidationFalsePositive(description) {
  const text = description.toLowerCase();
  return (
    text.includes("missing type validation") ||
    text.includes("missing input validation") ||
    text.includes("add input validation") ||
    text.includes("type check")
  );
}

function extractFunctionName(description) {
  const text = String(description || "");
  const patterns = [
    /\b(?:function|method|def)\s+([A-Za-z_]\w*)\b/i,
    /\bin\s+([A-Za-z_]\w*)\s*\(/i,
    /\bfor\s+([A-Za-z_]\w*)\s*\(/i
  ];
  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match?.[1]) {
      return match[1];
    }
  }
  return "global";
}

function looksLikePython(code) {
  const source = String(code || "");
  return /\bdef\s+[A-Za-z_]\w*\s*\(/.test(source) || /\bimport\s+[A-Za-z_]/.test(source);
}

function isGenericOverflowWarning(description, code) {
  const text = String(description || "").toLowerCase();
  if (!text.includes("overflow")) {
    return false;
  }
  if (!looksLikePython(code)) {
    return false;
  }
  return !/(32-bit|64-bit|fixed[-\s]?width|integer limit|c\+\+|cpp|javascript|typed array)/i.test(text);
}

function limitRuntimeNoise(items, maxPerFunction = 2) {
  const counts = new Map();
  const kept = [];
  for (const item of items) {
    const fnName = extractFunctionName(item.description);
    const current = counts.get(fnName) || 0;
    if (current >= maxPerFunction) {
      continue;
    }
    counts.set(fnName, current + 1);
    kept.push(item);
  }
  return kept;
}

function adjustScore(result) {
  const bugs = result?.bugs?.length || 0;
  const runtimeRisks = result?.runtime_risks?.length || 0;
  const codeIssues = result?.code_issues?.length || 0;

  if (bugs === 0 && runtimeRisks === 0 && codeIssues === 0) {
    return 10;
  }

  let score = 10;
  score -= bugs * 2;
  score -= runtimeRisks * 1;
  score -= codeIssues * 0.5;

  return Math.max(0, Math.round(score));
}

function isMinorSuggestion(description) {
  const text = String(description || "").toLowerCase();
  return (
    text.includes("docstring") ||
    text.includes("readability") ||
    text.includes("style") ||
    text.includes("naming") ||
    text.includes("comment") ||
    text.includes("best practice") ||
    text.includes("optional") ||
    text.includes("refactor")
  );
}

function dedupeAndNormalize(items, sectionType, typedCodePresent) {
  const map = new Map();
  const fallbackType = String(sectionType || "ITEM").toUpperCase();

  for (const raw of items || []) {
    const description = extractDescription(raw);
if (!description) continue; // ❗ DROP empty / fake items
    if (typedCodePresent && isTypeValidationFalsePositive(description)) {
      continue;
    }

    const itemType = extractItemType(raw, fallbackType);
    const codeSnippet = sectionType === "fix" ? extractFixCode(raw) : "";
    const normalizedKey = canonicalize(
      `${itemType} ${description.replace(/\b(docstring|docstrings)\b/gi, "docstring")}`
    );
    if (!normalizedKey) {
      continue;
    }

    if (!map.has(normalizedKey)) {
      map.set(normalizedKey, {
        raw,
        itemType,
        description,
        codeSnippet,
        duplicateCount: 1
      });
    } else {
      const existing = map.get(normalizedKey);
      existing.duplicateCount += 1;
      if (!existing.codeSnippet && codeSnippet) {
        existing.codeSnippet = codeSnippet;
      }
    }
  }

  return Array.from(map.values());
}

function ScoreRing({ score }) {
  const normalized = Math.max(0, Math.min(10, Number(score) || 0));
  const palette = getScorePalette(normalized);

  const radius = 56;
  const circumference = 2 * Math.PI * radius;
  const progress = normalized / 10;
  const dashOffset = circumference * (1 - progress);

  return (
    <div className="relative h-40 w-40">
      <svg viewBox="0 0 132 132" className="h-full w-full -rotate-90">
        <circle cx="66" cy="66" r={radius} fill="none" stroke="#1f2937" strokeWidth="8" />
        <circle
          cx="66"
          cy="66"
          r={radius}
          fill="none"
          stroke={palette.ring}
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={dashOffset}
          style={{ transition: "stroke-dashoffset 650ms ease, stroke 300ms ease" }}
        />
      </svg>
      <div className="absolute inset-0 grid place-items-center">
        <div className="text-center">
          <p className={`font-mono text-4xl ${palette.text}`}>{normalized.toFixed(1)}</p>
          <p className="text-xs text-app-muted">/ 10</p>
        </div>
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex h-full min-h-[320px] items-center justify-center border border-dashed border-app-border bg-app-panelAlt">
      <div className="flex flex-col items-center gap-3 px-6 text-center">
        <div className="grid h-10 w-10 place-items-center rounded-full border border-app-border bg-[#121c2d] text-app-muted">
          {"</>"}
        </div>
        <p className="text-sm font-medium text-app-text">Run analysis to see results</p>
        <p className="text-xs text-app-muted">Bugs, fixes, suggestions, and score will appear here.</p>
      </div>
    </div>
  );
}

function CleanState() {
  return (
    <div className="flex min-h-[220px] items-center justify-center rounded-lg border border-emerald-500/35 bg-emerald-500/10">
      <div className="px-6 text-center">
        <p className="text-sm font-medium text-emerald-200">No issues found</p>
        <p className="mt-1 text-xs text-emerald-300/80">Static analysis and AI review did not report actionable issues.</p>
      </div>
    </div>
  );
}

function getFixCopyText(item) {
  if (!item) {
    return "";
  }
  const codeValue = extractFixCode(item);
  if (codeValue) {
    return codeValue;
  }
  return extractDescription(item);
}

function ItemRow({ item, type, onApplyFix, onCopyFix, copied }) {
  const defaultType = String(type || "item").toUpperCase();
  const typeStyle =
    type === "bug"
      ? "border-red-500/30 bg-red-500/10 text-red-200"
      : type === "fix"
        ? "border-green-500/30 bg-green-500/10 text-green-200"
        : "border-cyan-500/30 bg-cyan-500/10 text-cyan-200";
  const itemType = item?.itemType || defaultType;
  const description = item?.description || "Analyzer returned an item without details.";
  const snippet = item?.codeSnippet || "";
  const duplicateCount = item?.duplicateCount || 1;

  return (
    <li className="rounded-md border border-app-border bg-[#0d1525] px-3 py-2.5 text-sm leading-6 text-app-text transition-colors duration-150 hover:border-slate-500 hover:bg-[#111a2b]">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className={`rounded-full border px-2 py-0.5 text-[11px] uppercase tracking-wide ${typeStyle}`}>
            {itemType}
          </span>
          {duplicateCount > 1 ? (
            <span className="rounded-full border border-app-border px-2 py-0.5 text-[10px] uppercase tracking-wide text-app-muted">
              x{duplicateCount}
            </span>
          ) : null}
        </div>
        {type === "fix" ? (
          <div className="flex items-center gap-2">
            {copied ? <span className="text-[11px] text-cyan-300">Copied!</span> : null}
            <button
              type="button"
              onClick={() => onCopyFix?.(item)}
              className="rounded-md border border-app-border bg-app-panelAlt px-2 py-1 text-[11px] font-medium uppercase tracking-wide text-app-text hover:border-slate-400"
            >
              Copy Fix
            </button>
            <button
              type="button"
              onClick={() => onApplyFix?.(item)}
              className="rounded-md border border-cyan-500/50 bg-cyan-500/10 px-2 py-1 text-[11px] font-medium uppercase tracking-wide text-cyan-200 hover:border-cyan-400 hover:bg-cyan-500/20"
            >
              Apply Fix
            </button>
          </div>
        ) : null}
      </div>
      <p className="text-sm text-app-text">{description}</p>
      {type === "fix" && snippet ? (
        <div className="mt-2 space-y-1">
          <p className="text-[11px] uppercase tracking-wide text-cyan-300">Suggested Fix (Full Code)</p>
          <pre className="overflow-x-auto rounded-md border border-app-border bg-[#0b1322] p-2 font-mono text-[12px] leading-5 text-cyan-100">
            <code>{snippet}</code>
          </pre>
        </div>
      ) : null}
    </li>
  );
}

function ListItems({ items, empty, type, onApplyFix, onCopyFix, copiedKey }) {
  if (!items?.length) {
    return <p className="text-sm text-app-muted">{empty}</p>;
  }

  return (
    <ul className="space-y-2">
      {items.map((item, idx) => (
        <ItemRow
          key={`${idx}-${item.itemType}-${item.description.slice(0, 12)}`}
          item={item}
          type={type}
          onApplyFix={() => onApplyFix?.(item.raw)}
          onCopyFix={() => onCopyFix?.(item.raw, idx)}
          copied={copiedKey === `${type}-${idx}`}
        />
      ))}
    </ul>
  );
}

export default function ResultsPanel({ loading, error, result, onApplyFix, code }) {
  const [showResults, setShowResults] = useState(false);
  const [copiedKey, setCopiedKey] = useState("");
  const hasValidResult = Boolean(result && typeof result === "object" && Array.isArray(result.files));

  const files = result?.files || [];
  const summary = result?.summary || {};
  const processingTime = result?.processing_time ?? 0;

  const typedParamHintsPresent = hasFunctionParamTypeHints(code);
  const mergedBugs = dedupeAndNormalize(files.flatMap((f) => f.bugs || []), "bug", typedParamHintsPresent);
  
  const mergedSuggestions = dedupeAndNormalize(files.flatMap((f) => f.suggestions || []), "suggestion", typedParamHintsPresent);
  const rawRuntimeBugs = mergedBugs.filter((item) =>
    /(runtime|exception|null|none|undefined|indexerror|keyerror|zerodivision|typeerror|valueerror|overflow|crash|memory|out of bounds)/i.test(
      item.description
    )
  );
  const filteredRuntimeBugs = rawRuntimeBugs.filter((item) => !isGenericOverflowWarning(item.description, code));
  const runtimeBugs = limitRuntimeNoise(filteredRuntimeBugs, 2);
  const logicBugs = mergedBugs.filter((item) => !rawRuntimeBugs.includes(item));
  let mergedFixes = dedupeAndNormalize(
  files.flatMap((f) => f.fixes || []),
  "fix",
  typedParamHintsPresent
).filter(f => f.codeSnippet);

// ✅ Ensure consistency: if issues exist → at least 1 fix
// if (
//   mergedFixes.length === 0 &&
//   (mergedBugs.length > 0 || runtimeBugs.length > 0 || logicBugs.length > 0)
// ) {
//   mergedFixes = [
//     {
//       itemType: "FIX",
//       description: "Add proper error handling or validation to fix the issue",
//       codeSnippet: code // fallback: show original code
//     }
//   ];
// }
// ❗ STRICT: Only allow REAL fixes
if (mergedFixes.length === 0) {
  mergedFixes = [];
}
  const hasIssues = mergedBugs.length > 0 || mergedFixes.length > 0 || mergedSuggestions.length > 0;
  const finalScore = adjustScore({
    bugs: mergedBugs,
    runtime_risks: runtimeBugs,
    code_issues: logicBugs
  });
  const palette = getScorePalette(finalScore);
  const scoreLabel = getScoreInterpretation(finalScore);

  function handleCopyFix(item, idx) {
    const text = getFixCopyText(item);
    if (!text) {
      return;
    }
    navigator.clipboard
      .writeText(text)
      .then(() => {
        const key = `fix-${idx}`;
        setCopiedKey(key);
        window.setTimeout(() => {
          setCopiedKey((current) => (current === key ? "" : current));
        }, 1200);
      })
      .catch(() => {
        setCopiedKey("");
      });
  }

  useEffect(() => {
    if (!result) {
      setShowResults(false);
      return;
    }
    setShowResults(false);
    const frame = requestAnimationFrame(() => setShowResults(true));
    return () => cancelAnimationFrame(frame);
  }, [result]);

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-xl border border-app-border bg-app-panel p-5 shadow-[0_10px_30px_rgba(2,6,23,0.18)]">
      {hasValidResult && !loading ? (
        <div className="sticky top-0 z-10 -mx-5 -mt-5 mb-4 border-b border-app-border bg-app-panel px-5 py-4">
          <p className="text-[11px] uppercase tracking-wide text-app-muted">Code Quality Score</p>
          <div className="mt-4 flex items-center gap-5">
            <ScoreRing score={finalScore} />
            <div className="flex-1 space-y-3">
              <div className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs ${palette.tone}`}>
                {scoreLabel}
              </div>
              <p className="text-sm leading-6 text-app-muted">Overall quality based on static + LLM review</p>
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-app-muted">
                <span>Errors: {summary.total_errors ?? 0}</span>
                <span>Warnings: {summary.total_warnings ?? 0}</span>
                <span>Time: {processingTime}ms</span>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {loading ? (
        <div className="mb-4 space-y-3">
          <div className="flex items-center gap-2 text-sm text-app-muted">
            <Spinner />
            Analyzing code...
          </div>
          <p className="text-xs text-app-muted">Running static analysis and LLM review.</p>
          <div className="space-y-2">
            <div className="h-11 animate-pulse border border-app-border bg-app-panelAlt" />
            <div className="h-11 animate-pulse border border-app-border bg-app-panelAlt" />
            <div className="h-11 animate-pulse border border-app-border bg-app-panelAlt" />
          </div>
        </div>
      ) : null}

      {error ? <div className="mb-4 border border-app-danger bg-app-panelAlt px-3 py-2.5 text-sm text-app-danger">{error}</div> : null}

      {!loading && !error && !hasValidResult ? <EmptyState /> : null}

      {!loading && hasValidResult ? (
        <div
          className={`space-y-3 overflow-y-auto pr-1 transition-opacity duration-200 ${
            showResults ? "opacity-100" : "opacity-0"
          }`}
        >
          <p className="text-[11px] uppercase tracking-wide text-app-muted">Review Findings</p>
          {!hasIssues ? (
            <CleanState />
          ) : (
            <div className="space-y-2 border-t border-app-border pt-4">
              <CollapsibleSection title="Runtime Risks" count={runtimeBugs.length}>
                <ListItems items={runtimeBugs} empty="No runtime risks reported." type="bug" />
              </CollapsibleSection>
              <CollapsibleSection title="Code Issues" count={logicBugs.length}>
                <ListItems items={logicBugs} empty="No additional code issues reported." type="bug" />
              </CollapsibleSection>
              <CollapsibleSection title="Fixes" count={mergedFixes.length}>
                <ListItems
                  items={mergedFixes}
                  empty="No fixes generated."
                  type="fix"
                  onApplyFix={onApplyFix}
                  onCopyFix={handleCopyFix}
                  copiedKey={copiedKey}
                />
              </CollapsibleSection>
              <CollapsibleSection title="Suggestions" count={mergedSuggestions.length}>
                <ListItems items={mergedSuggestions} empty="No suggestions generated." type="suggestion" />
              </CollapsibleSection>
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
