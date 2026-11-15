import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#07110e",
        panel: "#10211b",
        line: "#234536",
        mint: "#3dffb0",
        amber: "#ffb020",
        ember: "#ff5a36",
        foam: "#e7f6ef",
        mute: "#8eae9f",
      },
  fontFamily: {
    sans: ["Segoe UI", "Avenir Next", "Trebuchet MS", "sans-serif"],
    mono: ["Cascadia Mono", "Consolas", "ui-monospace", "monospace"],
  },
      boxShadow: {
        panel: "0 20px 60px rgba(0, 0, 0, 0.28)",
      },
    },
  },
  plugins: [],
};

export default config;
