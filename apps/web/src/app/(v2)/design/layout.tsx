import type { Metadata } from "next";
import "../../globals.css";
import React, { Suspense } from "react";

export const metadata: Metadata = {
  title: "Open SWE - Design",
  description: "Feature graph design canvas",
  icons: {
    icon: "/favicon.ico",
    shortcut: "/favicon.ico",
    apple: "/favicon.ico",
  },
};

export default function DesignLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return <Suspense fallback={<div>Loading...</div>}>{children}</Suspense>;
}

