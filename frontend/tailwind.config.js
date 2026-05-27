/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        app: {
          bg: "#0b1220",
          panel: "#0f172a",
          panelAlt: "#111827",
          border: "#1f2937",
          soft: "#334155",
          text: "#e5e7eb",
          muted: "#9ca3af",
          accent: "#22d3ee",
          danger: "#f87171",
          success: "#34d399"
        }
      },
      fontFamily: {
        sans: ["Inter", "Segoe UI", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "Consolas", "Menlo", "monospace"]
      }
    }
  },
  plugins: []
};

