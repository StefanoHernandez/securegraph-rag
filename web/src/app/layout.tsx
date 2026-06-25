import type { Metadata } from "next";
import "./globals.css";
import VaultBackground from "@/components/layout/VaultBackground";

export const metadata: Metadata = {
  title: "SecureGraph — Verifiable Hybrid RAG",
  description:
    "Graph-RAG with verifiable ReBAC access control, tamper-evident audit, and cross-retriever consensus.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark h-full antialiased">
      <body className="min-h-full flex flex-col relative overflow-x-hidden">
        <VaultBackground />
        {children}
      </body>
    </html>
  );
}
