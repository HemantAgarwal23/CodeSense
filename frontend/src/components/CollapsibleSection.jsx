import { useState } from "react";

export default function CollapsibleSection({ title, count, children, defaultOpen = true }) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <section className="overflow-hidden rounded-lg border border-app-border bg-app-panelAlt transition-colors duration-200">
      <button
        type="button"
        className="flex h-11 w-full items-center justify-between px-3 text-left text-sm text-app-text transition-colors duration-150 hover:bg-[#151e2d]"
        onClick={() => setOpen((prev) => !prev)}
      >
        <span className="flex items-center gap-2 font-medium tracking-tight">
          {title}
          <span className="rounded-full border border-app-border px-2 py-0.5 text-[11px] text-app-muted">{count}</span>
        </span>
        <span className="text-app-muted transition-transform duration-200">{open ? "−" : "+"}</span>
      </button>
      <div
        className={`grid overflow-hidden transition-[grid-template-rows] duration-200 ${
          open ? "grid-rows-[1fr]" : "grid-rows-[0fr]"
        }`}
      >
        <div className={`min-h-0 ${open ? "border-t border-app-border p-3" : ""}`}>{children}</div>
      </div>
    </section>
  );
}
