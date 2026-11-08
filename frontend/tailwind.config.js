/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        canvas: "#060812",
        panel: "#0b0f1f",
        raised: "#11172e",
        line: "#1c2442",
        "line-strong": "#2a3562",
        fg: "#e9ecf8",
        "fg-muted": "#98a3c7",
        "fg-dim": "#66719a",
        brand: "#8b9aff",
        "brand-strong": "#5b6cff",
        ok: "#4ade9a",
        warn: "#fbbf4d",
        bad: "#ff7b8c",
        info: "#5cc8ff",
      },
      fontFamily: {
        sans: [
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
      },
    },
  },
  plugins: [],
};
