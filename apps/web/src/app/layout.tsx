import type { Metadata } from "next";
import "./globals.css";
import { Inter } from "next/font/google";
import React from "react";
import { NuqsAdapter } from "nuqs/adapters/next/app";
import { ThemeProvider } from "@/components/theme-provider";
import { TaskPlanProvider } from "@/providers/TaskPlanProvider";

const inter = Inter({
  subsets: ["latin"],
  preload: true,
  display: "swap",
});

export const metadata: Metadata = {
  title: "Open SWE",
  description: "Open SWE UX by LangChain",
  icons: {
    icon: "/favicon.ico",
    shortcut: "/favicon.ico",
    apple: "/favicon.ico",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
    >
      <body className={inter.className}>
        <ThemeProvider
          defaultTheme="system"
          storageKey="theme"
        >
          <TaskPlanProvider>
            <NuqsAdapter>{children}</NuqsAdapter>
          </TaskPlanProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
