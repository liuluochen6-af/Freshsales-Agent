import type { Metadata } from "next";
import "./globals.css";
import "./workbench.css";

export const metadata: Metadata = {
  title: "Freshsales-Agent · Multi-Agent Sales Intelligence OS",
  description: "从客户消息到报价、库存、订单、履约、售后和合规的多 Agent 销售运营演示。",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
