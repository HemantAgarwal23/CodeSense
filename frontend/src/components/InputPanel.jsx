import { useRef, useState } from "react";

export default function InputPanel({
  code,
  repoUrl,
  maxFiles,
  uploadedFileName,
  canSubmit,
  loading,
  canUndo,
  fixStatus,
  codeUpdated,
  modifiedRange,
  onCodeChange,
  onRepoUrlChange,
  onMaxFilesChange,
  onSubmit,
  onUndo,
  onFileLoaded,
  onClearFile
}) {
  const fileInputRef = useRef(null);
  const [copyStatus, setCopyStatus] = useState("");
  const buttonBase =
    "inline-flex h-10 items-center justify-center rounded-md border px-3 text-sm font-medium transition-all duration-150";
  const outlineButton =
    `${buttonBase} border-app-border bg-app-panelAlt text-app-text hover:border-slate-400 hover:bg-[#1a2434] disabled:text-app-muted disabled:opacity-60`;
  const primaryButton =
    `${buttonBase} min-w-28 border-cyan-500 bg-cyan-500/20 px-4 font-semibold text-cyan-100 hover:border-cyan-400 hover:bg-cyan-500/30 active:translate-y-px active:bg-cyan-500/35 disabled:border-app-border disabled:bg-app-panelAlt disabled:text-app-muted disabled:opacity-60`;

  function handleCopyCode() {
    if (!code?.trim()) {
      return;
    }
    navigator.clipboard
      .writeText(code)
      .then(() => {
        setCopyStatus("Copied!");
        window.setTimeout(() => setCopyStatus(""), 1200);
      })
      .catch(() => {
        setCopyStatus("Copy failed");
        window.setTimeout(() => setCopyStatus(""), 1200);
      });
  }

  function handlePickFile() {
    fileInputRef.current?.click();
  }

  function handleFileChange(event) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }

    const allowedExtensions = [".py", ".js", ".cpp", ".java"];
    const lowerName = file.name.toLowerCase();
    const hasAllowedExtension = allowedExtensions.some((ext) => lowerName.endsWith(ext));
    if (!hasAllowedExtension) {
      event.target.value = "";
      return;
    }

    const reader = new FileReader();
    reader.onload = () => {
      const content = typeof reader.result === "string" ? reader.result : "";
      onFileLoaded?.(content, file.name);
      event.target.value = "";
    };
    reader.onerror = () => {
      event.target.value = "";
    };
    reader.readAsText(file);
  }

  return (
    <div className="flex h-full flex-col gap-4 rounded-xl border border-app-border bg-app-panel p-5 shadow-[0_10px_30px_rgba(2,6,23,0.18)]">
      <div className="rounded-lg border border-app-border bg-app-panelAlt p-4">
        <label className="mb-3 block text-[11px] uppercase tracking-wide text-app-muted">Upload</label>
        <div className="flex flex-wrap items-center gap-2">
          <input
            ref={fileInputRef}
            type="file"
            accept=".py,.js,.cpp,.java"
            className="hidden"
            onChange={handleFileChange}
          />
          <button
            type="button"
            onClick={handlePickFile}
            className={`${outlineButton} h-9 text-xs`}
          >
            {uploadedFileName ? "Replace File" : "Upload File"}
          </button>
          {uploadedFileName ? (
            <button
              type="button"
              onClick={onClearFile}
              className={`${outlineButton} h-9 text-xs`}
            >
              Clear File
            </button>
          ) : null}
        </div>
        <p className="mt-3 text-xs text-app-muted">{uploadedFileName ? `Loaded: ${uploadedFileName}` : "No file selected"}</p>
      </div>

      <div className="rounded-lg border border-app-border bg-app-panelAlt p-4">
        <label className="mb-3 block text-[11px] uppercase tracking-wide text-app-muted">Repository</label>
        <input
          type="text"
          value={repoUrl}
          onChange={(e) => onRepoUrlChange(e.target.value)}
          placeholder="https://github.com/owner/repo"
          className="h-10 w-full rounded-md border border-app-border bg-[#101827] px-3 text-sm text-app-text outline-none placeholder:text-app-muted hover:border-slate-500 focus:border-cyan-500 focus:ring-2 focus:ring-cyan-500/25"
        />
      </div>

      <div className="rounded-lg border border-app-border bg-app-panelAlt p-4">
        <label className="mb-3 block text-[11px] uppercase tracking-wide text-app-muted">Code</label>
        <textarea
          value={code}
          onChange={(e) => onCodeChange(e.target.value)}
          placeholder="Paste source code here..."
          className={`h-[360px] w-full resize-none rounded-md border px-4 py-3 font-mono text-[13px] leading-6 text-app-text outline-none placeholder:text-app-muted hover:border-slate-500 focus:border-cyan-500 focus:ring-2 focus:ring-cyan-500/30 ${
            codeUpdated ? "border-cyan-500 bg-[#162743]" : "border-app-border bg-[#0f1b2d]"
          }`}
        />
      </div>

      {fixStatus ? (
        <div className="rounded-lg border border-emerald-500/35 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200">
          <div className="font-medium">{fixStatus}</div>
          {modifiedRange ? <div className="mt-1 text-emerald-300/85">Modified lines: {modifiedRange}</div> : null}
        </div>
      ) : null}
      {copyStatus ? <div className="text-xs text-cyan-300">{copyStatus}</div> : null}

      <div className="mt-auto flex items-end justify-between gap-4 border-t border-app-border pt-4">
        <div className="w-28">
          <label className="mb-2 block text-[11px] uppercase tracking-wide text-app-muted">Max Files</label>
          <input
            type="number"
            min="1"
            value={maxFiles}
            onChange={(e) => onMaxFilesChange(e.target.value)}
            className="h-10 w-full rounded-md border border-app-border bg-[#101827] px-3 text-sm text-app-text outline-none placeholder:text-app-muted hover:border-slate-500 focus:border-cyan-500 focus:ring-2 focus:ring-cyan-500/25"
          />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={!code?.trim() || loading}
            onClick={handleCopyCode}
            className={`${outlineButton} min-w-24`}
          >
            Copy Code
          </button>
          <button
            type="button"
            disabled={!canUndo || loading}
            onClick={onUndo}
            className={`${outlineButton} min-w-20`}
          >
            Undo Fix
          </button>
          <button
            type="button"
            disabled={loading || !canSubmit}
            onClick={onSubmit}
            className={primaryButton}
          >
            {loading ? "Analyzing..." : "Analyze"}
          </button>
        </div>
      </div>
    </div>
  );
}
