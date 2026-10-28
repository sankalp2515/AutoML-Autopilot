import type { Config } from 'tailwindcss';

/**
 * "Obsidian Atelier" design system — luxury & futuristic, dual-theme.
 *
 * Every color is backed by a CSS variable (RGB triplet) defined in globals.css,
 * so the same class vocabulary renders correctly in dark (obsidian) and light
 * ("Ivory Atelier") themes. <html class="light"> switches the variables.
 */
function v(name: string) {
  return `rgb(var(--${name}) / <alpha-value>)`;
}

const config: Config = {
  content: [
    './pages/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        obsidian: {
          950: v('obsidian-950'),
          900: v('obsidian-900'),
          850: v('obsidian-850'),
          800: v('obsidian-800'),
          700: v('obsidian-700'),
          600: v('obsidian-600'),
        },
        bone: {
          DEFAULT: v('bone'),
          dim: v('bone-dim'),
          faint: v('bone-faint'),
          ghost: v('bone-ghost'),
        },
        gold: {
          200: v('gold-200'),
          300: v('gold-300'),
          400: v('gold-400'),
          500: v('gold-500'),
          600: v('gold-600'),
          700: v('gold-700'),
          900: v('gold-900'),
        },
        jade: {
          300: v('jade-300'),
          400: v('jade-400'),
          500: v('jade-500'),
          600: v('jade-600'),
          900: v('jade-900'),
        },
        terra: {
          300: v('terra-300'),
          400: v('terra-400'),
          500: v('terra-500'),
          900: v('terra-900'),
        },
      },
      fontFamily: {
        display: ['var(--font-display)', 'Georgia', 'serif'],
        sans: ['var(--font-body)', 'system-ui', 'sans-serif'],
        mono: ['var(--font-mono)', 'ui-monospace', 'monospace'],
      },
      letterSpacing: {
        luxe: '0.22em',
      },
    },
  },
  plugins: [],
};

export default config;
