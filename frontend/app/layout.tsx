import type { Metadata } from 'next';
import { Space_Grotesk, IBM_Plex_Sans, IBM_Plex_Mono } from 'next/font/google';
import './globals.css';
import AppShell from '@/components/AppShell';

// "Paper-lab" engineering instrument type system: Space Grotesk for display/brand,
// IBM Plex Sans for body, IBM Plex Mono for code/labels.
const display = Space_Grotesk({
  subsets: ['latin'],
  weight: ['500', '600', '700'],
  variable: '--font-display',
  display: 'swap',
});

const body = IBM_Plex_Sans({
  subsets: ['latin'],
  weight: ['400', '500', '600', '700'],
  variable: '--font-body',
  display: 'swap',
});

const mono = IBM_Plex_Mono({
  subsets: ['latin'],
  weight: ['400', '500', '600'],
  variable: '--font-mono',
  display: 'swap',
});

export const metadata: Metadata = {
  title: process.env.NEXT_PUBLIC_AUTOPILOT_ONLY === 'true' ? 'AutoML Autopilot' : 'AutoML Orchestrator',
  description: process.env.NEXT_PUBLIC_AUTOPILOT_ONLY === 'true'
    ? 'When a model breaks, run the experiment that teaches you the most, then prove the fix.'
    : 'Upload a CSV, state your goal — ten agents build and explain a deployable model.',
  icons: { icon: 'data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><circle cx=%2250%22 cy=%2250%22 r=%2240%22 fill=%22%236366f1%22/></svg>' },
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
