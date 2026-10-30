import type { Metadata } from 'next';
import localFont from 'next/font/local';
import './globals.css';
import './repair/repair.css';
import AppShell from '@/components/AppShell';

// "Paper-lab" engineering instrument type system: Space Grotesk for display/brand,
// IBM Plex Sans for body, IBM Plex Mono for code/labels.
// Licensed, bundled versions of the existing fonts make builds independent of
// Google Fonts response formats and network availability.
const display = localFont({
  src: [{ path: '../public/fonts/space-grotesk-latin.woff2', weight: '500 700', style: 'normal' }],
  variable: '--font-display', display: 'swap',
});
const body = localFont({
  src: [{ path: '../public/fonts/ibm-plex-sans-latin.woff2', weight: '400 700', style: 'normal' }],
  variable: '--font-body', display: 'swap',
});
const mono = localFont({
  src: [
    { path: '../public/fonts/ibm-plex-mono-400-latin.woff2', weight: '400', style: 'normal' },
    { path: '../public/fonts/ibm-plex-mono-500-latin.woff2', weight: '500', style: 'normal' },
    { path: '../public/fonts/ibm-plex-mono-600-latin.woff2', weight: '600', style: 'normal' },
  ],
  variable: '--font-mono', display: 'swap',
});

export const metadata: Metadata = {
  title: 'AutoML Autopilot',
  description: 'The ML coding agent that doesn’t stop at green tests. Inspect repository repairs with independent software and ML evidence.',
  icons: {
    icon: [{ url: '/autopilot-favicon.svg?v=1', type: 'image/svg+xml', sizes: 'any' }],
    shortcut: '/autopilot-favicon.svg?v=1',
  },
};

// Runs before paint — applies the saved theme so there is no flash of wrong theme.
const themeInitScript = `
(function() {
  try {
    var t = localStorage.getItem('atelier-theme');
    if (t === 'dark') {
      document.documentElement.classList.add('dark');
      document.documentElement.classList.remove('light');
    } else {
      document.documentElement.classList.add('light');
      document.documentElement.classList.remove('dark');
    }
  } catch (e) { document.documentElement.classList.add('light'); }
})();
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="light" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body
        className={`${display.variable} ${body.variable} ${mono.variable} font-sans antialiased`}
        style={{ background: 'var(--bg)', color: 'var(--t1)' }}
      >
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
