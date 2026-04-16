import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}"
  ],
  theme: {
    extend: {
      colors: {
        ink: "#102033",
        mist: "#f6f3eb",
        ember: "#ff7a18",
        lagoon: "#0b6e8a",
        pine: "#155246",
        sand: "#efe5d5",
        steel: "#44546a"
      },
      fontFamily: {
        sans: ["Avenir Next", "Segoe UI", "Helvetica Neue", "sans-serif"],
        serif: ["Iowan Old Style", "Palatino Linotype", "Book Antiqua", "serif"]
      },
      boxShadow: {
        panel: "0 24px 80px rgba(16, 32, 51, 0.14)",
        soft: "0 12px 32px rgba(13, 50, 76, 0.08)"
      },
      backgroundImage: {
        "dashboard-glow":
          "radial-gradient(circle at top left, rgba(255,122,24,0.22), transparent 34%), radial-gradient(circle at top right, rgba(11,110,138,0.18), transparent 36%), linear-gradient(180deg, rgba(255,255,255,0.96), rgba(246,243,235,0.92))"
      }
    }
  },
  plugins: []
};

export default config;
