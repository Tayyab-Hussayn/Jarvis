import type { Metadata, Viewport } from "next";

import "./globals.css";

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
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
