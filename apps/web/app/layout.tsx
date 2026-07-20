import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Atlas Research",
  description: "Agentic, cited knowledge-graph research",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
