/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          50: "#f5f6fa",
          100: "#e8ebf3",
          200: "#cdd3e3",
          300: "#9ba5c3",
          400: "#6c7796",
          500: "#475068",
          600: "#2f364b",
          700: "#1f2436",
          800: "#161a28",
          900: "#0d1018",
          950: "#070912",
        },
        accent: {
          DEFAULT: "#6366f1",
          50: "#eef0ff",
          200: "#c7c9ff",
          400: "#8b8eff",
          500: "#6366f1",
          600: "#5046e5",
          700: "#3f37c0",
        },
        teal: {
          glow: "#22d3ee",
        },
      },
      fontFamily: {
        sans: [
          "Inter",
          "SF Pro Text",
          "system-ui",
          "-apple-system",
          "sans-serif",
        ],
        mono: ["JetBrains Mono", "SF Mono", "Menlo", "monospace"],
      },
      boxShadow: {
        glow: "0 0 24px -8px rgba(99, 102, 241, 0.5)",
      },
    },
  },
  plugins: [],
};
