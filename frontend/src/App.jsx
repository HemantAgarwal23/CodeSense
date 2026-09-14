import { useMemo, useState } from "react";

import InputPanel from "./components/InputPanel";
import ResultsPanel from "./components/ResultsPanel";
import { runPullRequestReview, runReview } from "./lib/api";

function extractErrorMessage(error) {
  if (error?.code === "ECONNABORTED") {
    return "The review timed out. Reviewing many files can hit Groq's free-tier rate limits; try fewer files.";
  }
  if (!error?.response) {
    return "Cannot reach the API. Check that the backend is running on port 8000.";
  }
  const detail = error.response.data?.detail;
  if (typeof detail === "string") {
    return detail;
  }
  if (detail?.error?.message) {
    return detail.error.message;
  }
  if (Array.isArray(detail) && detail[0]?.msg) {
    return detail[0].msg;
  }
  return "Unable to process request.";
}

export default function App() {
  const [code, setCode] = useState("");
  const [repoUrl, setRepoUrl] = useState("");
  const [prUrl, setPrUrl] = useState("");
  const [postComments, setPostComments] = useState(false);
  const [maxFiles, setMaxFiles] = useState("5");
  const [uploadedFileName, setUploadedFileName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [previousCode, setPreviousCode] = useState(null);
  const [fixStatus, setFixStatus] = useState("");
  const [codeUpdated, setCodeUpdated] = useState(false);

  const canSubmit = useMemo(
    () => Boolean(code.trim() || repoUrl.trim() || prUrl.trim()),
    [code, repoUrl, prUrl]
  );

  async function handleAnalyze() {
    if (!canSubmit) {
      setError("Provide code, a repository URL, or a pull request URL.");
      return;
    }

    setLoading(true);
    setError("");
    setResult(null);

    const maxFilesValue = maxFiles ? Number(maxFiles) : undefined;
    try {
      const data = prUrl.trim()
        ? await runPullRequestReview({
            pr_url: prUrl.trim(),
            max_files: maxFilesValue,
            post_comments: postComments
          })
        : await runReview({
            provider: "groq",
            code: code.trim() || undefined,
            filename: uploadedFileName || undefined,
            repo_url: repoUrl.trim() || undefined,
            max_files: maxFilesValue
          });
      setResult(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  function triggerCodeHighlight() {
    setCodeUpdated(true);
    window.setTimeout(() => setCodeUpdated(false), 1200);
  }

  function handleCodeChange(nextCode) {
    setCode(nextCode);
    setResult(null);
  }

  function handleApplyFix(fix) {
    if (!fix?.code) {
      setFixStatus("No full-code fix available for this item.");
      return;
    }
    setPreviousCode(code);
    setCode(fix.code);
    setResult(null);
    setFixStatus("Fix applied successfully.");
    triggerCodeHighlight();
  }

  function handleUndoFix() {
    if (previousCode === null) {
      return;
    }
    setCode(previousCode);
    setResult(null);
    setPreviousCode(null);
    setFixStatus("Undo successful.");
    triggerCodeHighlight();
  }

  function handleFileLoaded(content, fileName) {
    handleCodeChange(content);
    setUploadedFileName(fileName);
    setFixStatus(`Loaded file: ${fileName}`);
    triggerCodeHighlight();
  }

  function handleClearFile() {
    setUploadedFileName("");
    handleCodeChange("");
    setFixStatus("File cleared.");
    triggerCodeHighlight();
  }

  return (
    <main className="min-h-screen bg-app-bg px-4 py-4 text-app-text md:px-6 md:py-6">
      <div className="mx-auto max-w-[1440px]">
        <header className="mb-6 rounded-xl border border-app-border bg-app-panel px-5 py-4 shadow-[0_10px_30px_rgba(2,6,23,0.22)]">
          <h1 className="text-xl font-semibold tracking-tight">CodeSense AI</h1>
          <p className="mt-1 text-sm text-app-muted">Static analysis + LLM code review</p>
        </header>

        <section className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_1px_minmax(0,1fr)] lg:gap-5">
          <div className="min-h-[620px]">
            <InputPanel
              code={code}
              repoUrl={repoUrl}
              prUrl={prUrl}
              postComments={postComments}
              maxFiles={maxFiles}
              uploadedFileName={uploadedFileName}
              canSubmit={canSubmit}
              loading={loading}
              canUndo={previousCode !== null}
              fixStatus={fixStatus}
              codeUpdated={codeUpdated}
              onCodeChange={handleCodeChange}
              onRepoUrlChange={setRepoUrl}
              onPrUrlChange={setPrUrl}
              onPostCommentsChange={setPostComments}
              onMaxFilesChange={setMaxFiles}
              onSubmit={handleAnalyze}
              onUndo={handleUndoFix}
              onFileLoaded={handleFileLoaded}
              onClearFile={handleClearFile}
            />
          </div>
          <div className="hidden rounded-full bg-app-border/70 lg:block" />
          <div className="min-h-[620px]">
            <ResultsPanel loading={loading} error={error} result={result} onApplyFix={handleApplyFix} />
          </div>
        </section>
      </div>
    </main>
  );
}
