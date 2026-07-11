import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Velora Labs",
  description: "AI assistant that indexes a GitHub repo and answers questions about its code (RAG over source code).",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}