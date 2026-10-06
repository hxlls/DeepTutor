"use client";

/**
 * 后台任务条 —— 导入试卷不再把用户锁在弹窗里。
 *
 * 增强导入的弹窗只负责「提交」：请求一返回（服务端已 `create_task`）就关闭，
 * 进度改由本组件在顶部持续显示。用户可以随便切页面，解析在服务端继续跑。
 *
 * 与 ModelDownloadBanner 的分工：
 *   · ModelDownloadBanner —— 管「装模型」（含手动下载直链清单）
 *   · 本组件             —— 管「导试卷」
 * 两者靠 /api/assistant/progress 的 `kind` 字段区分，不会同时出现。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Loader2,
  X,
} from "lucide-react";
import { requestJson } from "@/shared/api/client";

interface LogItem {
  kind: string;
  msg: string;
}

interface ProgressResp {
  logs: LogItem[];
  total: number;
  task_start: number;
  running: boolean;
  kind: string | null;
}

/** pipeline.py 输出的 `--- [3/6] 文件名 ---`，用来显示「正在处理第几个」 */
const FILE_RE = /^---\s*\[(\d+)\/(\d+)\]\s*(.+?)\s*---$/;
/** 入库阶段：`已导入 10/20` */
const STORE_RE = /^已导入\s+(\d+)\/(\d+)/;

/** 成功后自动隐藏的延时：够看清结果，又不会一直占着屏幕顶部 */
const AUTO_HIDE_MS = 6000;

export default function BackgroundTaskBar() {
  const [visible, setVisible] = useState(false);
  const [logs, setLogs] = useState<LogItem[]>([]);
  const [cursor, setCursor] = useState(0);
  const [running, setRunning] = useState(false);
  const [done, setDone] = useState(false);
  const [collapsed, setCollapsed] = useState(true);
  const [dismissed, setDismissed] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const poll = useCallback(async () => {
    try {
      const r = await requestJson<ProgressResp>(
        `/api/assistant/progress?since=${cursor}`,
      );

      // 只管导入任务，部署任务交给 ModelDownloadBanner
      if (r.kind !== "import") return;

      // 首次拉取（cursor=0）时跳过历史日志，只跟最近一次任务 ——
      // 否则刷新页面会把上一次任务（结尾是 done）整段读进来，
      // 一打开就显示「已完成」。
      if (cursor === 0) {
        if (r.task_start > 0) {
          setCursor(r.task_start);
          setRunning(r.running);
          setVisible(r.running);
        }
        return;
      }

      // 上一个任务已完成、现在又有任务在跑 → 是新的一轮，清掉残留。
      // （不能靠 task_start 比较判断：两个任务之间没有日志时它可能相等。）
      if (done && r.running) {
        setLogs([]);
        setDone(false);
        setDismissed(false);
        setCursor(r.task_start);
        setRunning(true);
        setVisible(true);
        return;
      }

      setRunning(r.running);

      if (r.logs?.length) {
        setLogs((prev) => [...prev.slice(-300), ...r.logs]);
        setCursor(r.total);
        setVisible(true);

        const last = r.logs[r.logs.length - 1];
        if (last.kind === "done") {
          setDone(true);
          setRunning(false);
          // 成功后自动淡出 —— 导入不像装模型需要复制十几个链接，
          // 停留太久反而挡视线。出错时不自动隐藏，留着让用户看原因。
          if (hideTimer.current) clearTimeout(hideTimer.current);
          hideTimer.current = setTimeout(() => setDismissed(true), AUTO_HIDE_MS);
        } else if (last.kind === "error") {
          setCollapsed(false);
        }
      }
    } catch {
      /* 忽略：轮询失败不该影响用户 */
    }
  }, [cursor, done]);

  useEffect(() => {
    const timer = setInterval(poll, 2000);
    return () => {
      clearInterval(timer);
      if (hideTimer.current) clearTimeout(hideTimer.current);
    };
  }, [poll]);

  useEffect(() => {
    if (logRef.current && !collapsed) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logs, collapsed]);

  // 从日志里推当前进度。
  // 分两个阶段：先逐个「解析」，再整批「入库」；入库阶段的日志更靠后，
  // 所以优先显示它 —— 否则解析到 6/6 之后会一直挂着「正在解析 6/6」。
  const { file, store } = (() => {
    let file: { i: number; total: number; name: string } | null = null;
    let store: string | null = null;
    for (let i = logs.length - 1; i >= 0; i--) {
      const msg = logs[i].msg;
      if (!store) {
        const s = msg.match(STORE_RE);
        if (s) store = `${s[1]}/${s[2]}`;
      }
      if (!file) {
        const m = msg.match(FILE_RE);
        if (m) file = { i: +m[1], total: +m[2], name: m[3] };
      }
      if (file && store) break;
    }
    return { file, store };
  })();

  // 错误只认日志级别 —— 任务内部的单文件失败是 info 级（会继续跑），
  // 只有整个任务失败才是 error。靠文案前缀判断会误报。
  const hasError = logs.length > 0 && logs[logs.length - 1].kind === "error";
  const doneMsg = [...logs].reverse().find((l) => l.kind === "done")?.msg;

  if (!visible || dismissed) return null;

  const title = done
    ? doneMsg || "导入已完成"
    : hasError
      ? "导入遇到问题，展开查看详情"
      : store
        ? `正在入库 ${store}`
        : file
          ? `正在解析 ${file.i}/${file.total} · ${file.name}`
          : "正在后台处理试卷…";

  return (
    <div
      className={`fixed inset-x-0 top-0 z-[89] border-b px-4 py-2 text-sm shadow-sm ${
        done
          ? "border-green-200 bg-green-50 dark:border-green-900 dark:bg-green-950/40"
          : hasError
            ? "border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950/40"
            : "border-sky-200 bg-sky-50 dark:border-sky-900 dark:bg-sky-950/40"
      }`}
    >
      <div className="flex items-center gap-3">
        {done ? (
          <CheckCircle2 className="h-4 w-4 shrink-0 text-green-600" />
        ) : hasError ? (
          <AlertCircle className="h-4 w-4 shrink-0 text-red-600" />
        ) : (
          <Loader2 className="h-4 w-4 shrink-0 animate-spin text-sky-600" />
        )}

        <span className="flex-1 truncate" title={title}>
          {title}
          {running && (
            <span className="ml-2 text-xs text-[var(--muted-foreground)]">
              后台运行中，可以先去做别的
            </span>
          )}
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

      {!collapsed && logs.length > 0 && (
        <div
          ref={logRef}
          className="mt-2 max-h-48 overflow-y-auto rounded bg-black/80 p-2 font-mono text-[11px] leading-relaxed text-green-300"
        >
          {logs.map((l, i) => (
            <div key={i}>{l.msg}</div>
          ))}
        </div>
      )}
    </div>
  );
}
