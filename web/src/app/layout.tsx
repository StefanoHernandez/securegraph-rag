import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import ThreeBackground from "@/components/layout/ThreeBackground";

const inter = Inter({
  variable: "--font-sans",
  subsets: ["latin"],
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "SecurGraph RAG — Trustworthy Hybrid RAG",
  description: "Enterprise Graph-RAG framework with verifiable ReBAC policy enforcement and cross-retriever consensus.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${jetbrainsMono.variable} dark h-full antialiased`}
    >
      <body className="min-h-full flex flex-col relative overflow-x-hidden">
        <ThreeBackground opacity={0.4} />
        {children}
      </body>
    </html>
  );
}
