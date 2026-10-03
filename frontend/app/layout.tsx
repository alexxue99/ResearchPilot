import type { Metadata } from 'next';
import { Geist, Geist_Mono } from 'next/font/google';
import './globals.css';
import 'katex/dist/katex.min.css';

const geistSans = Geist({ variable: '--font-geist-sans', subsets: ['latin'] });
const geistMono = Geist_Mono({ variable: '--font-geist-mono', subsets: ['latin'] });

export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? 'http://localhost:3000'),
  title: 'ResearchPilot — Agentic Research Workspace',
  description: 'Plan, execute, inspect, and audit evidence-backed mathematical and machine learning research.',
  icons: {
    icon: [{ url: '/favicon.svg', type: 'image/svg+xml' }],
    shortcut: '/favicon.svg',
  },
  openGraph: {
    title: 'ResearchPilot — Agentic Research Workspace',
    description: 'Evidence-backed AI research, from question to experiment.',
    images: [{url: '/og.png', width: 1733, height: 909, alt: 'ResearchPilot evidence-backed AI research workspace'}],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'ResearchPilot — Agentic Research Workspace',
    description: 'Evidence-backed AI research, from question to experiment.',
    images: ['/og.png'],
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body className={`${geistSans.variable} ${geistMono.variable}`}>{children}</body></html>;
}
