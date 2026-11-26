import type { Metadata } from "next";

import { Nav } from "@/components/nav";

import { Providers } from "./providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "OpsPilot",
  description: "Agentic triage copilot for open-source issue trackers",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="flex min-h-full flex-col font-sans">
        <Providers>
          <Nav />
          <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-6">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
