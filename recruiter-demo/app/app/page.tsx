import type { Metadata } from "next";
import Home from "../page";

export const metadata: Metadata = {
  title: "运营控制台 · Freshsales-Agent",
  description: "Freshsales-Agent 生产运营控制台：管理 Agent、市场数据、企业数据导入与安全路由。",
};

export default function OperationsApp() {
  return <div className="appRoute"><Home /></div>;
}
