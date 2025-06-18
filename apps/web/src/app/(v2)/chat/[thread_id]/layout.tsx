import type { Metadata } from "next";
import "../../../globals.css";
import React from "react";

export const metadata: Metadata = {
  title: "Open SWE - Thread",
  description: "Open SWE thread view",
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
  return children;
}
