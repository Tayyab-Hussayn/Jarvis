import type { Metadata, Viewport } from "next";
import { Inter_Tight, JetBrains_Mono, Unbounded } from "next/font/google";

import "./globals.css";

// next/font downloads these at build time and serves them from the export, so
// the LAN/offline UI never reaches Google at runtime. tokens.css reads the
// --nf-* variables and keeps concrete fallback families behind them.
const display = Unbounded({
  subsets: ["latin"],
  variable: "--nf-display",
  display: "swap",
});
const ui = Inter_Tight({
  subsets: ["latin"],
  variable: "--nf-ui",
  display: "swap",
});
const mono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--nf-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Kravein",
  description: "Linux system assistant",
};

// `resizes-content` makes the on-screen keyboard shrink the layout viewport
// (as dvh already does on desktop) instead of covering the fixed-height shell,
// which is what hid the composer behind the keyboard in the Android WebView.
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  interactiveWidget: "resizes-content",
  themeColor: "#080808",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${display.variable} ${ui.variable} ${mono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
