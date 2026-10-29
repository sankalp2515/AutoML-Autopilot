import type { Metadata } from 'next';
export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL || 'http://localhost:3000'),
  title: 'AutoML Autopilot · Investigate, test, verify',
  description: 'Find why a production model degraded. Run a cheap diagnostic experiment and independently verify the fix on unseen data.',
  openGraph: { title: 'AutoML Autopilot', description: 'The AI SRE for model quality, with a verifier that can say no.', images: ['/autopilot/opengraph-image'] },
  twitter: { card: 'summary_large_image', title: 'AutoML Autopilot', images: ['/autopilot/opengraph-image'] },
  icons: { icon: '/autopilot-mark.svg' },
};
export default function Layout({ children }: { children: React.ReactNode }) { return children; }
