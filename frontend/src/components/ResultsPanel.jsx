import { useEffect, useState } from "react";

import CollapsibleSection from "./CollapsibleSection";
import Spinner from "./Spinner";

const EMPTY_FINDINGS = { runtime_risks: [], code_issues: [], fixes: [], suggestions: [] };

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

function formatLocation(finding) {
  if (!finding?.file) {
    return "";
  }
  return finding.line ? `${finding.file}:${finding.line}` : finding.file;
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

function PullRequestSummary({ pullRequest, commentCount }) {
  return (
    <div className="rounded-lg border border-app-border bg-app-panelAlt p-3 text-xs text-app-muted">
      <p className="text-[11px] uppercase tracking-wide">Pull Request</p>
      <a
        href={pullRequest.url}
        target="_blank"
        rel="noreferrer"
        className="mt-1 block text-sm font-medium text-cyan-200 hover:underline"
      >
        {pullRequest.title || pullRequest.url}
      </a>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        <span>
          Files reviewed: {pullRequest.files_reviewed} of {pullRequest.files_changed}
        </span>
        <span>Findings on changed lines: {commentCount}</span>
        {pullRequest.comments_posted ? (
          <span className="text-emerald-300">Posted {pullRequest.comments_posted} comments to GitHub</span>
        ) : null}
      </div>
    </div>
  );
}

function ItemRow({ item, type, canApplyFix, onApplyFix, onCopyFix, copied }) {
  const typeStyle =
    type === "bug"
      ? "border-red-500/30 bg-red-500/10 text-red-200"
      : type === "fix"
        ? "border-green-500/30 bg-green-500/10 text-green-200"
        : "border-cyan-500/30 bg-cyan-500/10 text-cyan-200";
  const location = formatLocation(item);

  return (
    <li className="rounded-md border border-app-border bg-[#0d1525] px-3 py-2.5 text-sm leading-6 text-app-text transition-colors duration-150 hover:border-slate-500 hover:bg-[#111a2b]">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className={`rounded-full border px-2 py-0.5 text-[11px] uppercase tracking-wide ${typeStyle}`}>
            {item.label}
          </span>
          <span className="rounded-full border border-app-border px-2 py-0.5 text-[10px] tracking-wide text-app-text">
            Severity: {String(item.severity).toUpperCase()}
          </span>
          <span className="rounded-full border border-app-border px-2 py-0.5 text-[10px] tracking-wide text-app-muted">
            Confidence: {String(item.confidence).toUpperCase()}
          </span>
          {item.count > 1 ? (
            <span className="rounded-full border border-app-border px-2 py-0.5 text-[10px] uppercase tracking-wide text-app-muted">
              x{item.count}
            </span>
          ) : null}
        </div>
        {type === "fix" ? (
          <div className="flex items-center gap-2">
            {copied ? <span className="text-[11px] text-cyan-300">Copied!</span> : null}
            <button
              type="button"
              onClick={onCopyFix}
              className="rounded-md border border-app-border bg-app-panelAlt px-2 py-1 text-[11px] font-medium uppercase tracking-wide text-app-text hover:border-slate-400"
            >
              Copy Fix
            </button>
            {canApplyFix ? (
              <button
                type="button"
                onClick={onApplyFix}
                className="rounded-md border border-cyan-500/50 bg-cyan-500/10 px-2 py-1 text-[11px] font-medium uppercase tracking-wide text-cyan-200 hover:border-cyan-400 hover:bg-cyan-500/20"
              >
                Apply Fix
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
      <p className="text-sm text-app-text">{item.description}</p>
      {location ? <p className="mt-1 text-xs text-app-muted">Location: {location}</p> : null}
      {type === "fix" && item.code ? (
        <div className="mt-2 space-y-1">
          <p className="text-[11px] uppercase tracking-wide text-cyan-300">Suggested Fix (Full Code)</p>
          <pre className="overflow-x-auto rounded-md border border-app-border bg-[#0b1322] p-2 font-mono text-[12px] leading-5 text-cyan-100">
            <code>{item.code}</code>
          </pre>
        </div>
      ) : null}
    </li>
  );
}

function ListItems({ items, empty, type, canApplyFix, onApplyFix, onCopyFix, copiedKey }) {
  if (!items.length) {
    return <p className="text-sm text-app-muted">{empty}</p>;
  }

  return (
    <ul className="space-y-2">
      {items.map((item, idx) => (
        <ItemRow
          key={`${type}-${idx}-${item.label}`}
          item={item}
          type={type}
          canApplyFix={canApplyFix}
          onApplyFix={() => onApplyFix?.(item)}
          onCopyFix={() => onCopyFix?.(item, idx)}
          copied={copiedKey === `${type}-${idx}`}
        />
      ))}
    </ul>
  );
}

export default function ResultsPanel({ loading, error, result, onApplyFix }) {
  const [showResults, setShowResults] = useState(false);
  const [copiedKey, setCopiedKey] = useState("");
  const hasValidResult = Boolean(result && typeof result === "object" && Array.isArray(result.files));

  const summary = result?.summary || {};
  const isFailureState = String(summary.status || "ok").toLowerCase() === "failed";
  const processingTime = result?.processing_time ?? 0;
  const findings = { ...EMPTY_FINDINGS, ...(result?.findings || {}) };
  const { runtime_risks: runtimeRisks, code_issues: codeIssues, fixes, suggestions } = findings;
  const pullRequest = result?.pull_request?.url ? result.pull_request : null;
  const reviewComments = result?.review_comments || [];

  const hasIssues = runtimeRisks.length + codeIssues.length + fixes.length + suggestions.length > 0;
  const finalScore = Math.max(0, Math.min(10, Number(summary.final_score) || 0));
  const palette = getScorePalette(finalScore);
  const scoreLabel = getScoreInterpretation(finalScore);
  const failureReason = suggestions[0]?.description || "Check the input and retry.";

  function handleCopyFix(item, idx) {
    const text = item?.code || item?.description;
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
          {pullRequest ? <PullRequestSummary pullRequest={pullRequest} commentCount={reviewComments.length} /> : null}
          <p className="text-[11px] uppercase tracking-wide text-app-muted">Review Findings</p>
          {isFailureState ? (
            <div className="rounded-lg border border-red-500/40 bg-red-500/10 p-4 text-sm text-red-200">
              Review failed: {failureReason}
            </div>
          ) : !hasIssues ? (
            <CleanState />
          ) : (
            <div className="space-y-2 border-t border-app-border pt-4">
              <CollapsibleSection title="Runtime Risks" count={runtimeRisks.length}>
                <ListItems items={runtimeRisks} empty="No runtime risks reported." type="bug" />
              </CollapsibleSection>
              <CollapsibleSection title="Code Issues" count={codeIssues.length}>
                <ListItems items={codeIssues} empty="No additional code issues reported." type="bug" />
              </CollapsibleSection>
              <CollapsibleSection title="Fixes" count={fixes.length}>
                <ListItems
                  items={fixes}
                  empty="No fixes generated."
                  type="fix"
                  canApplyFix={!pullRequest}
                  onApplyFix={onApplyFix}
                  onCopyFix={handleCopyFix}
                  copiedKey={copiedKey}
                />
              </CollapsibleSection>
              <CollapsibleSection title="Suggestions" count={suggestions.length}>
                <ListItems items={suggestions} empty="No suggestions generated." type="suggestion" />
              </CollapsibleSection>
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
