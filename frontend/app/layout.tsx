import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "GridPulse",
  description: "Real-time peak-energy micro-bounties for a cleaner grid.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans antialiased">{children}</body>
    </html>
  );
}
