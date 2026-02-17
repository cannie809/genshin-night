/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'werewolf-dark': '#1a1a1a',
        'werewolf-accent': '#646cff',
      },
    },
  },
  plugins: [],
}
