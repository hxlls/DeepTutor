"use client";

/**
 * 模型下载横幅 —— 全局显示后台部署进度。
 *
 * 引导页点「开始部署」后不再阻塞，立即进入主界面；
 * 本组件轮询 /api/assistant/progress，把进度显示在顶部。
 * 下载完成后横幅变成「已就绪」并在几秒后自动消失。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, ChevronDown, ChevronUp, Loader2, X } from "lucide-react";
import { requestJson } from "@/shared/api/client";
import ModelLinksPanel from "./ModelLinksPanel";

interface LogItem {
  kind: string;
  msg: string;
}

export default function ModelDownloadBanner() {
  const [visible, setVisible] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const [cursor, setCursor] = useState(0);
  const [done, setDone] = useState(false);
  // 默认展开：横幅里最有用的是「下载太慢」的手动下载清单，
  // 藏在折叠里用户根本不知道有这回事。嫌占地方可以点右上角收起。
  const [collapsed, setCollapsed] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  const doneTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const poll = useCallback(async () => {
    try {
      const r = await requestJson<{
        logs: LogItem[];
        total: number;
        task_start: number;
        running: boolean;
      }>(`/api/assistant/progress?since=${cursor}`);

      // 首次拉取（cursor=0）时跳过历史日志，只跟最近一次任务。
      // 否则刷新页面会把上一次任务（结尾是 done）整段读进来，
      // 一打开就显示「已完成」，真正的下载进度反而看不到。
      if (cursor === 0 && r.task_start > 0) {
        setCursor(r.task_start);
        return;
      }

      if (r.running) setVisible(true);

      if (r.logs?.length) {
        setLogs((prev) => [...prev.slice(-300), ...r.logs.map((l) => l.msg)]);
        setCursor(r.total);
        setVisible(true);

        // 出现完成/错误标记时收尾
        const last = r.logs[r.logs.length - 1];
        if (last.kind === "done") {
          setDone(true);
          // 自动展开日志：环境已就绪时整个部署几秒就跑完（全在"跳过下载"），
          // 不展开的话用户只看到"已完成"，会以为漏了下载步骤。
          setCollapsed(false);
          // 完成后**不自动隐藏**：横幅里有手动下载清单，用户可能要复制
          // 十几个链接（合计 2GB），几秒钟根本不够。让他自己点关闭。
          if (doneTimer.current) {
            clearTimeout(doneTimer.current);
            doneTimer.current = null;
          }
        } else if (last.kind === "error") {
          // 出错时保持显示，让用户看到原因
          setCollapsed(false);
        }
      }
    } catch {
      /* 忽略 */
    }
  }, [cursor]);

  useEffect(() => {
    const timer = setInterval(poll, 2000);
    return () => {
      clearInterval(timer);
      if (doneTimer.current) clearTimeout(doneTimer.current);
    };
  }, [poll]);

  useEffect(() => {
    if (logRef.current && !collapsed) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logs, collapsed]);

  // 从日志末尾推一个进度百分比（Ollama 拉模型时会输出 xx%）。
  //
  // 只认「末尾连续」的百分比：bge-m3 下到 99% 之后会进入 MinerU 安装阶段，
  // 那段没有百分比输出，如果还沿用旧值，横幅会一直挂着 99% —— 看着像卡死。
  // 末尾连续几条都没有 % 就认为进入了无进度的阶段，不再显示百分比。
  const percent = (() => {
    let scanned = 0;
    for (let i = logs.length - 1; i >= 0; i--) {
      const m = logs[i].match(/(\d{1,3})%/);
      if (m) return Math.min(100, parseInt(m[1], 10));
      if (++scanned >= 3) break;
    }
    return null;
  })();

  const hasError = logs.some((l) => l.startsWith("错误") || l.includes("失败"));

  if (!visible || dismissed) return null;

  return (
    <div
      className={`fixed inset-x-0 top-0 z-[90] border-b px-4 py-2 text-sm shadow-sm ${
        done
          ? "border-green-200 bg-green-50 dark:border-green-900 dark:bg-green-950/40"
          : hasError
            ? "border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950/40"
            : "border-amber-200 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/40"
      }`}
    >
      <div className="flex items-center gap-3">
        {done ? (
          <CheckCircle2 className="h-4 w-4 shrink-0 text-green-600" />
        ) : (
          <Loader2 className="h-4 w-4 shrink-0 animate-spin text-amber-600" />
        )}

        <span className="flex-1 truncate">
          {done
            ? "配置已完成，现在可以导入试卷了"
            : hasError
              ? "部署遇到问题，展开查看详情"
              : `正在下载模型${percent !== null ? ` ${percent}%` : "..."}` +
                " · 完成后自动启用，期间可以先熟悉界面"}
        </span>

        <button
          onClick={() => setCollapsed((v) => !v)}
          className="rounded p-1 hover:bg-black/5"
          title={collapsed ? "展开日志" : "收起日志"}
        >
          {collapsed ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronUp className="h-4 w-4" />
          )}
        </button>

        {(done || hasError) && (
          <button
            onClick={() => setDismissed(true)}
            className="rounded p-1 hover:bg-black/5"
            title="关闭"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      {/* 展开区：手动下载清单 + 日志，统一受右上角「收起」控制。
          加高度上限，避免内容多时把整个屏幕挡住。 */}
      {!collapsed && (
        <div className="max-h-[60vh] overflow-y-auto">
          <ModelLinksPanel />
          {logs.length > 0 && (
            <div
              ref={logRef}
              className="mt-2 max-h-40 overflow-y-auto rounded bg-black/80 p-2 font-mono text-[11px] leading-relaxed text-green-300"
            >
              {logs.map((l, i) => (
                <div key={i}>{l}</div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
