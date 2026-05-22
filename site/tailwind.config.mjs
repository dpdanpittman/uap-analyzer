/** @type {import('tailwindcss').Config} */
export default {
  content: ['./src/**/*.{astro,html,js,jsx,md,mdx,ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // Deep void — FLIR scope at night
        bg: '#06080b',
        'bg-soft': '#0a0d12',
        panel: '#0d1117',
        'panel-soft': '#11161d',
        'panel-warm': '#13110a',

        // Body text — slightly green-cool white, terminal feel
        text: '#c8d0db',
        muted: '#6c7280',
        'muted-bright': '#909aa6',

        // FLIR amber — primary accent
        accent: '#f0b46a',
        'accent-bright': '#ffce7a',
        'accent-dim': '#8a6638',

        // Terminal phosphor — secondary accent
        'accent-green': '#7cc474',
        'accent-green-bright': '#9be091',

        // Classified red — alerts, warnings, redaction stamps
        warm: '#d0524a',
        'warm-bright': '#ff7a70',

        // Structural
        border: '#1a2028',
        'border-bright': '#2a3340',
        redaction: '#000000',
        classified: '#3a0a0a',

        // Code / readouts — CRT scope
        'code-bg': '#040608',
        'code-border': '#15191f',
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'Consolas', 'monospace'],
        display: ['JetBrains Mono', 'Fira Code', 'Consolas', 'monospace'],
      },
      backgroundImage: {
        // Scanline overlay — CRT phosphor
        scanlines: 'repeating-linear-gradient(0deg, rgba(255,255,255,0.018) 0px, rgba(255,255,255,0.018) 1px, transparent 1px, transparent 3px)',
        // Subtle grid for FLIR-recticle feel
        'flir-grid': 'linear-gradient(rgba(240,180,106,0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(240,180,106,0.03) 1px, transparent 1px)',
      },
      boxShadow: {
        'amber-glow': '0 0 24px -8px rgba(240, 180, 106, 0.4)',
        'green-glow': '0 0 20px -6px rgba(124, 196, 116, 0.35)',
        'red-glow': '0 0 24px -8px rgba(208, 82, 74, 0.5)',
      },
    },
  },
  plugins: [],
};
