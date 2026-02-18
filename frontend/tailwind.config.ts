import type { Config } from 'tailwindcss';

const config: Config = {
  content: [
    './pages/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        'ndl-blue': '#003DA5',
        'ndl-light': '#F5F5F5',
      },
    },
  },
  plugins: [],
};

export default config;
