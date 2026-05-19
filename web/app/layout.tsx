import "./globals.css";
import type { ReactNode } from "react";

export const metadata = {
  title: "Spreadsheet Agent / 表格智能体",
  description: "AI-native spreadsheet ETL + semantic query",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh">
      <body className="bg-slate-50 text-slate-900">{children}</body>
    </html>
  );
}
