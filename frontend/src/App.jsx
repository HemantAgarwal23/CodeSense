import { useEffect, useMemo, useState } from "react";

import InputPanel from "./components/InputPanel";
import ResultsPanel from "./components/ResultsPanel";
import { runReview } from "./lib/api";

function extractErrorMessage(error) {
  const detail = error?.response?.data?.detail;
  if (typeof detail === "string") {
    return detail;
  }
  if (detail?.error?.message) {
    return detail.error.message;
  }
  return "Unable to process request.";
}

export default function App() {
  const [code, setCode] = useState("");
  const [repoUrl, setRepoUrl] = useState("");
  const [maxFiles, setMaxFiles] = useState("20");
  const [uploadedFileName, setUploadedFileName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [previousCode, setPreviousCode] = useState(null);
  const [fixStatus, setFixStatus] = useState("");
  const [codeUpdated, setCodeUpdated] = useState(false);
  const [modifiedRange, setModifiedRange] = useState("");
  const [fixes, setFixes] = useState([]);
  const [suggestions, setSuggestions] = useState([]);
  const [issues, setIssues] = useState([]);
  const [runtimeRisks, setRuntimeRisks] = useState([]);
  const [score, setScore] = useState(null);
  const [fixApplied, setFixApplied] = useState(false);

  useEffect(() => {
    setResult(null);
  }, []);

  useEffect(() => {
    setFixApplied(false);
  }, [code]);

  const canSubmit = useMemo(() => Boolean(code.trim() || repoUrl.trim()), [code, repoUrl]);

  async function handleAnalyze() {
    if (!canSubmit) {
      setError("Provide either code or a repository URL.");
      return;
    }

    setLoading(true);
    setError("");
    setFixes([]);
    setSuggestions([]);
    setIssues([]);
    setRuntimeRisks([]);
    setScore(null);
    setResult(null);

    try {
      const payload = {
        provider: "groq",
        code: code.trim() || undefined,
        repo_url: repoUrl.trim() || undefined,
        max_files: maxFiles ? Number(maxFiles) : undefined
      };
      const data = await runReview(payload);
      setResult(data);
      const files = data?.files || [];
      const fileBugs = files.flatMap((item) => item?.bugs || []);
      const fileFixes = files.flatMap((item) => item?.fixes || []);
      const fileSuggestions = files.flatMap((item) => item?.suggestions || []);
      const runtime = fileBugs.filter((entry) => {
        const text =
          typeof entry === "string"
            ? entry
            : entry?.description || entry?.message || entry?.title || entry?.reason || entry?.text || "";
        return /(runtime|exception|null|none|undefined|typeerror|valueerror|zerodivision|indexerror|keyerror)/i.test(
          String(text)
        );
      });

      setIssues(fileBugs);
      setFixes(fileFixes);
      setSuggestions(fileSuggestions);
      setRuntimeRisks(runtime);
      const backendScore = Number(data?.summary?.final_score ?? data?.final_score ?? 0);
      const staticScore = Math.max(
        0,
        10 - (fileBugs.length * 2 + runtime.length * 1.5)
      );
      setScore(Number((staticScore * 0.6 + backendScore * 0.4).toFixed(1)));
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  function extractFixCode(fix) {
    if (!fix || typeof fix !== "object") {
      return "";
    }
    const candidates = [
      fix.fix_code,
      fix.fixed_code,
      fix.replacement_code,
      fix.updated_code,
      fix.code,
      fix.snippet
    ];
    return String(candidates.find((value) => typeof value === "string" && value.trim()) || "").trim();
  }

  function extractFixText(fix) {
    if (typeof fix === "string") {
      return fix.trim() || "Suggested improvement.";
    }
    if (fix && typeof fix === "object") {
      const candidates = [fix.description, fix.message, fix.title, fix.reason, fix.text];
      return String(candidates.find((value) => typeof value === "string" && value.trim()) || "Suggested improvement.").trim();
    }
    return "Suggested improvement.";
  }

  function triggerCodeHighlight() {
    setCodeUpdated(true);
    window.setTimeout(() => setCodeUpdated(false), 1200);
  }

  function handleCodeChange(nextCode) {
    setCode(nextCode);
    setFixes([]);
    setSuggestions([]);
    setIssues([]);
    setRuntimeRisks([]);
    setScore(null);
    setFixApplied(false);
    setResult(null);
  }

  function getLanguageCommentPrefix(inputCode) {
    const value = String(inputCode || "");
    if (/\b(def|import|from)\b/.test(value)) {
      return "#";
    }
    if (/\b(function|const|let|var)\b|=>/.test(value)) {
      return "//";
    }
    return "#";
  }

  function countIndent(line) {
    const match = line.match(/^\s*/);
    return match ? match[0].length : 0;
  }

  function findPythonFunctionRange(inputCode, functionName) {
    const lines = inputCode.split("\n");
    const startRegex = new RegExp(`^\\s*def\\s+${functionName}\\s*\\(`);

    let start = -1;
    for (let i = 0; i < lines.length; i += 1) {
      if (startRegex.test(lines[i])) {
        start = i;
        break;
      }
    }
    if (start === -1) {
      return null;
    }

    const baseIndent = countIndent(lines[start]);
    let end = lines.length - 1;
    for (let i = start + 1; i < lines.length; i += 1) {
      const line = lines[i];
      const trimmed = line.trim();
      if (!trimmed) {
        continue;
      }
      const indent = countIndent(line);
      if (indent <= baseIndent && (trimmed.startsWith("def ") || trimmed.startsWith("class "))) {
        end = i - 1;
        break;
      }
    }
    return { start, end };
  }

  function findFirstPythonFunctionRange(inputCode) {
    const lines = inputCode.split("\n");
    for (let i = 0; i < lines.length; i += 1) {
      if (/^\s*def\s+[A-Za-z_]\w*\s*\(/.test(lines[i])) {
        const nameMatch = lines[i].match(/^\s*def\s+([A-Za-z_]\w*)\s*\(/);
        if (!nameMatch?.[1]) {
          continue;
        }
        return findPythonFunctionRange(inputCode, nameMatch[1]);
      }
    }
    return null;
  }

  function getFunctionInsertionIndex(lines, range) {
    // Start insertion after definition and any existing docstring block.
    let index = range.start + 1;
    while (index <= range.end && lines[index] && !lines[index].trim()) {
      index += 1;
    }

    const candidate = lines[index]?.trim();
    if (candidate?.startsWith('"""') || candidate?.startsWith("'''")) {
      const quote = candidate.startsWith('"""') ? '"""' : "'''";
      if (candidate.endsWith(quote) && candidate.length > 3) {
        return index + 1;
      }
      index += 1;
      while (index <= range.end && !lines[index].includes(quote)) {
        index += 1;
      }
      return Math.min(index + 1, range.end + 1);
    }
    return index;
  }

  function extractFunctionParams(defLine) {
    const match = defLine.match(/def\s+[A-Za-z_]\w*\s*\((.*)\)\s*:/);
    if (!match?.[1]) {
      return [];
    }
    return match[1]
      .split(",")
      .map((part) => part.trim())
      .map((part) => part.replace(/=.*/, "").replace(/^\*+/, "").trim())
      .filter((name) => name && name !== "self" && name !== "cls");
  }

  function applyDocstringFix(currentCode, range) {
    const lines = currentCode.split("\n");
    const defLine = lines[range.start];
    const baseIndent = " ".repeat(countIndent(defLine));
    const bodyIndent = `${baseIndent}    `;
    const insertAt = getFunctionInsertionIndex(lines, range);
    const existingNearTop = lines
      .slice(range.start + 1, Math.min(range.start + 5, range.end + 1))
      .some((line) => line.trim().startsWith('"""') || line.trim().startsWith("'''"));
    if (existingNearTop) {
      return null;
    }

    const docLines = [`${bodyIndent}"""TODO: add function docstring."""`];
    const updatedLines = [...lines.slice(0, insertAt), ...docLines, ...lines.slice(insertAt)];
    return {
      code: updatedLines.join("\n"),
      range: `${insertAt + 1}-${insertAt + docLines.length}`
    };
  }

  function applyInputValidationFix(currentCode, range) {
    const lines = currentCode.split("\n");
    const defLine = lines[range.start];
    let params = extractFunctionParams(defLine);
    const hasAB = /\b[aA]\b/.test(defLine) && /\b[bB]\b/.test(defLine);
    if (hasAB) {
      // Keep it simple and safe for common binary-function patterns.
      params = ["a", "b"];
    }
    if (!params.length) {
      return null;
    }

    const baseIndent = " ".repeat(countIndent(defLine));
    const bodyIndent = `${baseIndent}    `;
    const validationLines = [];
    for (const param of params) {
      const alreadyExists = lines
        .slice(range.start, range.end + 1)
        .some((line) => line.includes(`${param} is None`) || line.includes(`"${param} is required"`));
      if (alreadyExists) {
        continue;
      }
      validationLines.push(`${bodyIndent}if ${param} is None:`);
      validationLines.push(`${bodyIndent}    raise ValueError("${param} is required")`);
    }

    if (!validationLines.length) {
      return null;
    }

    const insertAt = getFunctionInsertionIndex(lines, range);
    const updatedLines = [...lines.slice(0, insertAt), ...validationLines, ...lines.slice(insertAt)];
    return {
      code: updatedLines.join("\n"),
      range: `${insertAt + 1}-${insertAt + validationLines.length}`
    };
  }

  function extractReturnBinaryOperator(blockCode) {
    const lines = String(blockCode || "").split("\n");
    for (const line of lines) {
      const trimmed = line.trim();
      const match = trimmed.match(/^return\s+([A-Za-z_]\w*)\s*([+\-])\s*([A-Za-z_]\w*)\s*$/);
      if (match) {
        return {
          left: match[1],
          operator: match[2],
          right: match[3]
        };
      }
    }
    return null;
  }

  function isUnsafeArithmeticChange(originalBlock, patchBlock) {
    const before = extractReturnBinaryOperator(originalBlock);
    const after = extractReturnBinaryOperator(patchBlock);
    if (!before || !after) {
      return false;
    }
    const sameOperands = before.left === after.left && before.right === after.right;
    return sameOperands && before.operator === "+" && after.operator === "-";
  }

  function applyMissingReturnFix(currentCode, range) {
    const lines = currentCode.split("\n");
    const hasReturn = lines.slice(range.start, range.end + 1).some((line) => /^\s*return\b/.test(line));
    if (hasReturn) {
      return null;
    }

    const defLine = lines[range.start];
    const baseIndent = " ".repeat(countIndent(defLine));
    const bodyIndent = `${baseIndent}    `;
    const returnLine = `${bodyIndent}return None`;
    const insertAt = range.end + 1;
    const updatedLines = [...lines.slice(0, insertAt), returnLine, ...lines.slice(insertAt)];
    return {
      code: updatedLines.join("\n"),
      range: `${insertAt + 1}-${insertAt + 1}`
    };
  }

  function applySmartFixFromDescription(currentCode, fixText, fixObject) {
    const description = String(fixText || "").toLowerCase();
    if (!description) {
      return null;
    }

    const explicitName =
      typeof fixObject === "object" && fixObject
        ? String(fixObject.function_name || fixObject.function || fixObject.target || "").trim()
        : "";
    const targetRange = explicitName
      ? findPythonFunctionRange(currentCode, explicitName)
      : findFirstPythonFunctionRange(currentCode);
    if (!targetRange) {
      return null;
    }

    if (description.includes("docstring")) {
      return applyDocstringFix(currentCode, targetRange);
    }
    if (description.includes("input validation")) {
      return applyInputValidationFix(currentCode, targetRange);
    }
    if (description.includes("missing return")) {
      return applyMissingReturnFix(currentCode, targetRange);
    }
    return null;
  }

  function extractTargetFunctionName(fixCode, fixText, fixObject) {
    if (fixObject && typeof fixObject === "object") {
      const explicitName = fixObject.function_name || fixObject.function || fixObject.target;
      if (typeof explicitName === "string" && explicitName.trim()) {
        return explicitName.trim();
      }
    }

    const fromFixCode = String(fixCode || "").match(/^\s*def\s+([A-Za-z_]\w*)\s*\(/m);
    if (fromFixCode?.[1]) {
      return fromFixCode[1];
    }

    const fromFixText = String(fixText || "").match(/\bfunction\s+([A-Za-z_]\w*)\b|\bdef\s+([A-Za-z_]\w*)\b/i);
    if (fromFixText?.[1] || fromFixText?.[2]) {
      return (fromFixText[1] || fromFixText[2]).trim();
    }
    return "";
  }

  function integrateFixCode(currentCode, fixCode, fixText, fixObject) {
    const codeValue = String(currentCode || "");
    const patch = String(fixCode || "").trim();
    if (!patch) {
      return null;
    }

    const targetName = extractTargetFunctionName(patch, fixText, fixObject);
    if (targetName) {
      const range = findPythonFunctionRange(codeValue, targetName);
      if (range) {
        const lines = codeValue.split("\n");
        const replacement = patch.split("\n");
        const originalBlock = lines.slice(range.start, range.end + 1).join("\n");
        if (isUnsafeArithmeticChange(originalBlock, patch)) {
          return null;
        }
        const updatedLines = [...lines.slice(0, range.start), ...replacement, ...lines.slice(range.end + 1)];
        return {
          code: updatedLines.join("\n"),
          range: `${range.start + 1}-${range.start + replacement.length}`,
          mode: "block"
        };
      }
    }

    const originalLineCount = codeValue.split("\n").length;
    if (isUnsafeArithmeticChange(codeValue, patch)) {
      return null;
    }
    return {
      code: patch,
      range: `1-${Math.max(1, patch.split("\n").length)}`,
      mode: originalLineCount > 0 ? "full" : "new"
    };
  }

  function appendSuggestionComment(currentCode, suggestionText) {
    const description = String(suggestionText || "").trim() || "Suggested improvement.";
    const prefix = getLanguageCommentPrefix(currentCode);
    const commentLines = description
      .split("\n")
      .map((line) => `${prefix} ${line.trim()}`)
      .join("\n");
    const separator = String(currentCode || "").trim() ? "\n\n" : "";
    const code = `${currentCode}${separator}${commentLines}`;
    const startLine = currentCode ? currentCode.split("\n").length + (separator ? 2 : 1) : 1;
    const endLine = startLine + commentLines.split("\n").length - 1;
    return { code, range: `${startLine}-${endLine}` };
  }

  function computeModifiedRange(beforeCode, afterCode) {
    const beforeLines = String(beforeCode ?? "").split("\n");
    const afterLines = String(afterCode ?? "").split("\n");

    if (beforeLines.join("\n") === afterLines.join("\n")) {
      return "";
    }

    let start = 0;
    const minLen = Math.min(beforeLines.length, afterLines.length);
    while (start < minLen && beforeLines[start] === afterLines[start]) {
      start += 1;
    }

    let endBefore = beforeLines.length - 1;
    let endAfter = afterLines.length - 1;
    while (endBefore >= start && endAfter >= start && beforeLines[endBefore] === afterLines[endAfter]) {
      endBefore -= 1;
      endAfter -= 1;
    }

    const displayStart = start + 1;
    const displayEnd = Math.max(displayStart, endAfter + 1);
    return `${displayStart}-${displayEnd}`;
  }

  function handleApplyFix(fix) {
    const fixCode = extractFixCode(fix);
    const fixText = extractFixText(fix);

    setPreviousCode(code);

    if (fixCode) {
      setCode(fixCode);
      setResult(null);
      setModifiedRange("");
      setFixStatus("Fix applied successfully.");
      setFixApplied(true);
      triggerCodeHighlight();
      return;
    }

    setFixStatus(fixText ? "No full-code fix available for this item." : "No full-code fix available.");
    setFixApplied(false);
  }

  function handleUndoFix() {
    if (previousCode === null) {
      return;
    }
    setCode(previousCode);
    setResult(null);
    setPreviousCode(null);
    setFixStatus("Undo successful.");
    setModifiedRange("");
    triggerCodeHighlight();
  }

  function handleFileLoaded(content, fileName) {
    handleCodeChange(content);
    setUploadedFileName(fileName);
    setFixStatus(`Loaded file: ${fileName}`);
    setModifiedRange("");
    triggerCodeHighlight();
  }

  function handleClearFile() {
    setUploadedFileName("");
    handleCodeChange("");
    setFixStatus("File cleared.");
    setModifiedRange("");
    triggerCodeHighlight();
  }

  return (
    <main className="min-h-screen bg-app-bg px-4 py-4 text-app-text md:px-6 md:py-6">
      <div className="mx-auto max-w-[1440px]">
        <header className="mb-6 rounded-xl border border-app-border bg-app-panel px-5 py-4 shadow-[0_10px_30px_rgba(2,6,23,0.22)]">
          <h1 className="text-xl font-semibold tracking-tight">Code Review Copilot</h1>
          <p className="mt-1 text-sm text-app-muted">Static analysis + LLM</p>
        </header>

        <section className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_1px_minmax(0,1fr)] lg:gap-5">
          <div className="min-h-[620px]">
            <InputPanel
              code={code}
              repoUrl={repoUrl}
              maxFiles={maxFiles}
              uploadedFileName={uploadedFileName}
              canSubmit={canSubmit}
              loading={loading}
              canUndo={previousCode !== null}
              fixStatus={fixStatus}
              codeUpdated={codeUpdated}
              modifiedRange={modifiedRange}
              onCodeChange={handleCodeChange}
              onRepoUrlChange={setRepoUrl}
              onMaxFilesChange={setMaxFiles}
              onSubmit={handleAnalyze}
              onUndo={handleUndoFix}
              onFileLoaded={handleFileLoaded}
              onClearFile={handleClearFile}
            />
          </div>
          <div className="hidden rounded-full bg-app-border/70 lg:block" />
          <div className="min-h-[620px]">
            <ResultsPanel loading={loading} error={error} result={result} onApplyFix={handleApplyFix} code={code} />
          </div>
        </section>
      </div>
    </main>
  );
}
