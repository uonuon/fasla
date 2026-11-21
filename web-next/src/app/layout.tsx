import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Fasla — know exactly what to produce next season",
  description:
    "Production planning for fashion brands: how many of each style, size and colour to make, how low your cash will get, and which courier to use. Built from your own orders.",
  openGraph: {
    title: "Fasla — know exactly what to produce next season",
    description: "Turn your past orders into a production plan, a cash forecast and the best courier per zone. Free during early access.",
    type: "website",
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        {/* eslint-disable-next-line @next/next/no-page-custom-font */}
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
