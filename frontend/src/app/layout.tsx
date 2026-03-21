import type { Metadata } from "next";
import { IBM_Plex_Mono, Manrope, Noto_Sans_Thai } from "next/font/google";
import "./globals.css";

const uiFont = Manrope({
  variable: "--font-ui",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
});

const monoFont = IBM_Plex_Mono({
  variable: "--font-code",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
});

const thaiFont = Noto_Sans_Thai({
  variable: "--font-thai",
  subsets: ["thai"],
  weight: ["300", "400", "500", "600", "700"],
});

export const metadata: Metadata = {
  title: "Network Copilot",
  description: "AI-powered network operations assistant for device monitoring and troubleshooting",
  icons: { icon: "/logo.svg" },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body className={`${uiFont.variable} ${monoFont.variable} ${thaiFont.variable} bg-background text-foreground antialiased`}>
        {children}
      </body>
    </html>
  );
}
