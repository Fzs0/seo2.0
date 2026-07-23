import type { ReactNode } from "react";
import type { Channel } from "../types/knowledge";

export const channelLabels: Record<Channel, string> = {
  seo: "SEO",
  social: "社交媒体",
  forum: "论坛",
  video: "视频",
  visual: "图文",
};

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "发生了未知错误，请稍后重试。";
}

export function formatDate(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString("zh-CN");
}

export function formatStructuredList(items: unknown[]): string {
  if (items.length === 0) return "未限定";
  return items.map((item) => typeof item === "string" ? item : JSON.stringify(item)).join("；");
}

export function PageHeading({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return (
    <div className="page-heading">
      <div><h1>{title}</h1><p>{description}</p></div>
      {action}
    </div>
  );
}

export function EmptyState({ title, text }: { title: string; text: string }) {
  return <div className="empty"><span aria-hidden="true">◇</span><strong>{title}</strong><p>{text}</p></div>;
}

export function LoadingRows() {
  return <div className="loading-rows" aria-label="正在加载"><span /><span /><span /></div>;
}

export function LoadingCard() {
  return <div className="card loading-card" aria-label="正在加载"><span /><span /><span /><span /></div>;
}

export function BatchMetric({ label, value, tone = "" }: { label: string; value: number; tone?: string }) {
  return <div className={`batch-metric ${tone}`}><span>{label}</span><strong>{value}</strong></div>;
}
