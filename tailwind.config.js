/** Bondhon MAS - Tailwind configuration. Brand colours come from the Bondhon logo (crimson + taupe). */
module.exports = {
  content: [
    "./templates/**/*.html",
    "./*/templates/**/*.html",
    "./*/forms.py",
    "./*/views.py",
    "./*/templatetags/*.py",
    "./static/src/**/*.js",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#fdf2f3", 100: "#fbe4e6", 200: "#f7ccd0", 300: "#f0a3aa", 400: "#e6707b",
          500: "#d6414f", 600: "#c1272d", 700: "#a11f26", 800: "#861d24", 900: "#721c23",
        },
        taupe: {
          50: "#f8f6f4", 100: "#efebe6", 200: "#e0d8cf", 300: "#cbbfb0", 400: "#b3a08b",
          500: "#9c8672", 600: "#85705e", 700: "#6d5b4d", 800: "#5a4c41", 900: "#2b2420", 950: "#1c1714",
        },
      },
      fontFamily: {
        sans: ['"Noto Sans Bengali"', '"Segoe UI"', "system-ui", "-apple-system", "Roboto", '"Helvetica Neue"', "Arial", "sans-serif"],
      },
    },
  },
  plugins: [require("@tailwindcss/forms")],
};
