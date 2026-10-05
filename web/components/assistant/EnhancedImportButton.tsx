"use client";

/**
 * 增强导入按钮 —— 挂在知识库的「链接文件夹」旁边。
 *
 * 支持两类输入：
 *   · 文档目录（PDF/DOCX/PPTX/XLSX）→ MinerU 解析 → 残留图片走 MiMo 兜底
 *   · Markdown 目录                 → 直接检查图片引用 → MiMo 兜底
 *
 * 引擎选择由引导时确定的部署方式决定，这里只展示当前用的是哪个。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  FileText,
  FolderOpen,
  Image as ImageIcon,
  Loader2,
  Sparkles,
  X,
} from "lucide-react";
import { apiUrl, requestJson } from "@/shared/api/client";

/** 浏览器选文件夹时前端先筛一道，后端还会再筛一次 */
const DOC_RE = /\.(pdf|docx|pptx|xlsx|md)$/i;

/**
 * 带上传进度的 POST。
 *
 * fetch 拿不到上传进度，而一个试卷文件夹可能有几十上百 MB，
 * 没进度用户会以为卡死了。所以这里用 XHR。
 */
function uploadWithProgress(
  url: string,
  form: FormData,
  onPct: (n: number) => void,
): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.withCredentials = true;
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        onPct(Math.round((e.loaded / e.total) * 100));
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText));
        } catch {
          resolve(null);
        }
      } else {
        reject(new Error(`HTTP ${xhr.status}`));
      }
    };
    xhr.onerror = () => reject(new Error("网络错误"));
    xhr.send(form);
  });
}

interface DocItem {
  path: string;
  name: string;
  ext: string;
  engine: string;
}

interface ScanDocsResult {
  ok: boolean;
  docs?: DocItem[];
  needs_convert?: { path: string; name: string; hint: string }[];
  images?: { path: string; name: string; hint: string }[];
  mineru_available?: boolean;
  msg?: string;
}

interface Props {
  kbName: string;
  onDone?: () => void;
}

export default function EnhancedImportButton({ kbName, onDone }: Props) {
  const [open, setOpen] = useState(false);
  const [dir, setDir] = useState("");
  const [scanning, setScanning] = useState(false);
  const [scan, setScan] = useState<ScanDocsResult | null>(null);
  const [running, setRunning] = useState(false);
  const [useFallback, setUseFallback] = useState(true);
  const [logs, setLogs] = useState<string[]>([]);
  const [cursor, setCursor] = useState(0);
  const [done, setDone] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);

  // 从浏览器直接选文件夹（拿不到绝对路径，所以走上传）
  const dirInputRef = useRef<HTMLInputElement>(null);
  const [picked, setPicked] = useState<File[]>([]);
  const [uploadPct, setUploadPct] = useState(0);

  // webkitdirectory / directory 是非标准属性，不在 React 的类型定义里，
  // 用 imperative 方式设置（官方 FileDropZone 也是这么做的）。
  const setDirInput = useCallback((el: HTMLInputElement | null) => {
    dirInputRef.current = el;
    if (el) {
      el.setAttribute("webkitdirectory", "");
      el.setAttribute("directory", "");
    }
  }, []);

  const onPickDir = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const all = Array.from(e.target.files ?? []);
    setPicked(all.filter((f) => DOC_RE.test(f.name)));
    setScan(null);
    setDone(false);
    setUploadPct(0);
  }, []);

  const append = useCallback((line: string) => {
    setLogs((prev) => [...prev.slice(-300), line]);
  }, []);

  // 轮询进度
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(async () => {
      try {
        const r = await requestJson<{ logs: { msg: string }[]; total: number }>(
          `/api/assistant/progress?since=${cursor}`,
        );
        if (r.logs?.length) {
          r.logs.forEach((l) => append(l.msg));
          setCursor(r.total);
          const last = r.logs[r.logs.length - 1];
          if (last.msg.includes("导入完成") || last.msg.includes("完成：")) {
            setDone(true);
            setRunning(false);
          }
        }
      } catch {
        /* 忽略 */
      }
    }, 1500);
    return () => clearInterval(timer);
  }, [running, cursor, append]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [logs]);

  const doScan = async () => {
    if (!dir.trim()) {
      alert("请填写服务器上的文件夹路径");
      return;
    }
    setScanning(true);
    setScan(null);
    setDone(false);
    try {
      const r = await requestJson<ScanDocsResult>("/api/assistant/scan-docs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dir: dir.trim() }),
      });
      setScan(r);
    } catch (e) {
      setScan({ ok: false, msg: String(e) });
    } finally {
      setScanning(false);
    }
  };

  const pickedTotal = picked.reduce((n, f) => n + f.size, 0);
  const fmtSize = (n: number) =>
    n < 1024 * 1024
      ? `${Math.max(1, Math.round(n / 1024))} KB`
      : `${(n / 1024 / 1024).toFixed(1)} MB`;

  const doImport = async () => {
    const fromBrowser = picked.length > 0;
    if (!fromBrowser && !scan?.docs?.length) return;

    setRunning(true);
    setLogs([]);
    setCursor(0);
    setUploadPct(0);
    setDone(false);

    try {
      if (fromBrowser) {
        // 浏览器选的文件：先传上去，再由服务端走同一套增强导入
        const form = new FormData();
        form.append("kb", kbName);
        form.append("use_fallback", String(useFallback));
        picked.forEach((f) =>
          form.append("files", f, f.webkitRelativePath || f.name),
        );
        append(`正在上传 ${picked.length} 个文件（${fmtSize(pickedTotal)}）...`);
        const r = (await uploadWithProgress(
          apiUrl("/api/assistant/import-upload"),
          form,
          setUploadPct,
        )) as { ok?: boolean; msg?: string; received?: number } | null;
        if (!r?.ok) throw new Error(r?.msg || "上传失败");
        append(`已上传 ${r.received ?? picked.length} 个文件，开始解析...`);
      } else {
        await requestJson("/api/assistant/import-docs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            kb: kbName,
            dir: dir.trim(),
            use_fallback: useFallback,
          }),
        });
        append("导入任务已启动...");
      }
      onDone?.();
    } catch (e) {
      append(`失败: ${e}`);
      setRunning(false);
    }
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-2 rounded-lg border border-[var(--border)] px-4 py-2 text-sm hover:bg-[var(--muted)]"
        title="导入试卷：自动解析公式、补齐图片内容"
      >
        <Sparkles className="h-4 w-4" />
        增强导入
      </button>
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-xl bg-[var(--background)] p-6 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-base font-medium">增强导入 · {kbName}</h3>
          <button
            onClick={() => !running && setOpen(false)}
            disabled={running}
            className="rounded p-1 hover:bg-[var(--muted)] disabled:opacity-40"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <p className="mb-4 text-xs text-[var(--muted-foreground)]">
          支持 PDF / Word / PPT / Excel，会自动解析公式（MinerU）
          并补齐残留图片内容（视觉模型）。
        </p>

        {/* 方式一：服务器上已有的文件夹 */}
        <div className="mb-2 flex gap-2">
          <input
            value={dir}
            onChange={(e) => setDir(e.target.value)}
            placeholder="/srv/试卷（服务器上的路径）"
            disabled={running}
            className="flex-1 rounded-lg border border-[var(--border)] bg-transparent px-3 py-2 text-sm outline-none focus:border-[var(--primary)]"
          />
          <button
            onClick={doScan}
            disabled={scanning || running}
            className="rounded-lg border border-[var(--border)] px-4 py-2 text-sm hover:bg-[var(--muted)] disabled:opacity-50"
          >
            {scanning ? <Loader2 className="h-4 w-4 animate-spin" /> : "扫描"}
          </button>
        </div>

        <div className="mb-3 flex items-center gap-3 text-[11px] text-[var(--muted-foreground)]">
          <span className="h-px flex-1 bg-[var(--border)]" />
          <span>试卷在你自己电脑上？</span>
          <span className="h-px flex-1 bg-[var(--border)]" />
        </div>

        {/* 方式二：从浏览器选文件夹。
            浏览器拿不到本地绝对路径（安全限制），所以选完是「上传」到服务器。 */}
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <input
            ref={setDirInput}
            type="file"
            multiple
            onChange={onPickDir}
            disabled={running}
            className="hidden"
          />
          <button
            onClick={() => dirInputRef.current?.click()}
            disabled={running}
            className="inline-flex items-center gap-2 rounded-lg border border-[var(--border)] px-4 py-2 text-sm hover:bg-[var(--muted)] disabled:opacity-50"
          >
            <FolderOpen className="h-4 w-4" />
            选择文件夹
          </button>
          {picked.length > 0 && (
            <>
              <span className="text-xs text-[var(--muted-foreground)]">
                已选 <b>{picked.length}</b> 个文档 · {fmtSize(pickedTotal)}
              </span>
              {!running && (
                <button
                  onClick={() => setPicked([])}
                  className="text-xs text-[var(--muted-foreground)] underline"
                >
                  清除
                </button>
              )}
            </>
          )}
        </div>

        {uploadPct > 0 && uploadPct < 100 && (
          <div className="mb-3">
            <div className="h-1.5 w-full overflow-hidden rounded bg-[var(--muted)]">
              <div
                className="h-full bg-[var(--primary)] transition-all"
                style={{ width: `${uploadPct}%` }}
              />
            </div>
            <p className="mt-1 text-[11px] text-[var(--muted-foreground)]">
              上传中 {uploadPct}%
            </p>
          </div>
        )}

        {/* 扫描结果 */}
        <div className="min-h-0 flex-1 overflow-y-auto">
          {scan && !scan.ok && (
            <p className="mb-3 text-sm text-red-500">{scan.msg}</p>
          )}

          {scan?.ok && (
            <>
              {scan.docs && scan.docs.length > 0 ? (
                <>
                  <p className="mb-2 text-sm">
                    发现 <b>{scan.docs.length}</b> 个文档
                    {!scan.mineru_available &&
                      scan.docs.some((d) => d.engine === "mineru") && (
                        <span className="ml-2 text-amber-600">
                          （MinerU 未安装，导入时会自动安装）
                        </span>
                      )}
                  </p>
                  <div className="mb-3 max-h-40 overflow-y-auto rounded-lg border border-[var(--border)]">
                    {scan.docs.map((it) => (
                      <div
                        key={it.path}
                        className="flex items-center gap-2 border-b border-[var(--border)] px-3 py-2 text-xs last:border-0"
                      >
                        <FileText className="h-3.5 w-3.5 shrink-0 text-[var(--muted-foreground)]" />
                        <span className="flex-1 truncate">{it.name}</span>
                        <span className="shrink-0 text-[var(--muted-foreground)]">
                          {it.engine === "mineru" ? "MinerU" : "直接用"}
                        </span>
                      </div>
                    ))}
                  </div>
                </>
              ) : (
                <p className="mb-3 text-sm text-[var(--muted-foreground)]">
                  没有找到可导入的文档
                </p>
              )}

              {/* 需转换的文件 */}
              {scan.needs_convert && scan.needs_convert.length > 0 && (
                <div className="mb-3 rounded-lg border border-amber-300 bg-amber-50 p-3 dark:bg-amber-950/30">
                  <p className="mb-1 text-xs font-medium text-amber-800 dark:text-amber-300">
                    ⚠ {scan.needs_convert.length} 个文件格式不支持，请先转换：
                  </p>
                  {scan.needs_convert.slice(0, 5).map((it) => (
                    <p key={it.path} className="text-[11px] text-amber-700 dark:text-amber-400">
                      {it.name} —— {it.hint}
                    </p>
                  ))}
                </div>
              )}

            </>
          )}

          {/* 兜底开关：无论文件来自服务器目录还是本地文件夹都适用 */}
          {(picked.length > 0 || (scan?.docs?.length ?? 0) > 0) && (
            <label className="mb-3 flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                checked={useFallback}
                onChange={(e) => setUseFallback(e.target.checked)}
                disabled={running}
                className="h-4 w-4"
              />
              <span>
                用视觉模型补齐残留图片
                <span className="ml-1 text-[var(--muted-foreground)]">
                  （MinerU 解析后通常仍有几何图等残留，建议开启）
                </span>
              </span>
            </label>
          )}

          {/* 日志 */}
          {logs.length > 0 && (
            <div
              ref={logRef}
              className="max-h-48 overflow-y-auto rounded-lg bg-black/80 p-3 font-mono text-xs leading-relaxed text-green-300"
            >
              {logs.map((l, i) => (
                <div key={i}>{l}</div>
              ))}
            </div>
          )}
        </div>

        <div className="mt-4 flex justify-end gap-2 border-t border-[var(--border)] pt-4">
          <button
            onClick={() => setOpen(false)}
            disabled={running}
            className="rounded-lg px-4 py-2 text-sm hover:bg-[var(--muted)] disabled:opacity-40"
          >
            {done ? "关闭" : "取消"}
          </button>
          <button
            onClick={doImport}
            disabled={running || (!picked.length && !scan?.docs?.length)}
            className="inline-flex items-center gap-2 rounded-lg bg-[var(--primary)] px-4 py-2 text-sm text-[var(--primary-foreground)] disabled:opacity-50"
          >
            {running && <Loader2 className="h-4 w-4 animate-spin" />}
            {running ? "处理中…" : done ? "已完成" : "开始导入"}
          </button>
        </div>
      </div>
    </div>
  );
}
