"use client";

/**
 * 增强导入按钮 —— 挂在知识库的「链接文件夹」旁边。
 *
 * 支持两类输入：
 *   · 文档目录（PDF/DOCX/PPTX/XLSX）→ MinerU 解析 → 残留图片走 MiMo 兜底
 *   · Markdown 目录                 → 直接检查图片引用 → MiMo 兜底
 *
 * 引擎选择由引导时确定的部署方式决定，这里只展示当前用的是哪个。
 *
 * ⚠️ 本弹窗**只负责提交**：请求一返回（服务端已 `create_task`）就自动关闭，
 *    解析在后台继续跑，进度由顶部的 BackgroundTaskBar 显示。
 *    早先的版本会一直轮询日志、并把关闭按钮锁到"导入完成"，
 *    用户被迫盯着进度条 —— 那是错的，几十份试卷要跑十几分钟。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  FileText,
  FolderOpen,
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
  // 只覆盖「上传 + 提交」这段。提交成功后服务端已经 create_task，
  // 前端不再等待，所以这个状态会立刻归位。
  const [submitting, setSubmitting] = useState(false);
  const [useFallback, setUseFallback] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);
  // 打开弹窗时若已有导入任务在后台跑，给个提示，避免重复提交
  const [busyInBackground, setBusyInBackground] = useState(false);

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

  // 打开时探一次：是否已有导入任务在后台运行
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await requestJson<{
          running: boolean;
          kind: string | null;
        }>("/api/assistant/progress");
        if (!cancelled) {
          setBusyInBackground(r.running && r.kind === "import");
        }
      } catch {
        /* 忽略：探测失败不影响导入 */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open]);

  const onPickDir = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const all = Array.from(e.target.files ?? []);
    setPicked(all.filter((f) => DOC_RE.test(f.name)));
    setScan(null);
    setNotice(null);
    setUploadPct(0);
  }, []);

  const doScan = async () => {
    if (!dir.trim()) {
      alert("请填写服务器上的文件夹路径");
      return;
    }
    setScanning(true);
    setScan(null);
    setNotice(null);
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

    setSubmitting(true);
    setNotice(null);
    setUploadPct(0);

    try {
      if (fromBrowser) {
        // 浏览器选的文件：先传上去，再由服务端走同一套增强导入。
        // 上传本身要时间（几十上百 MB），这段必须等；
        // 但服务端一收到就返回，后面的解析不在请求里。
        const form = new FormData();
        form.append("kb", kbName);
        form.append("use_fallback", String(useFallback));
        picked.forEach((f) =>
          form.append("files", f, f.webkitRelativePath || f.name),
        );
        const r = (await uploadWithProgress(
          apiUrl("/api/assistant/import-upload"),
          form,
          setUploadPct,
        )) as { ok?: boolean; msg?: string; received?: number } | null;
        if (!r?.ok) throw new Error(r?.msg || "上传失败");
      } else {
        const r = (await requestJson("/api/assistant/import-docs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            kb: kbName,
            dir: dir.trim(),
            use_fallback: useFallback,
          }),
        })) as { ok?: boolean; msg?: string } | null;
        if (!r?.ok) throw new Error(r?.msg || "提交失败");
      }

      // 服务端已 create_task，解析在后台跑 —— 立刻放人走
      setPicked([]);
      setOpen(false);
      onDone?.();
    } catch (e) {
      setNotice(`失败：${e}`);
    } finally {
      setSubmitting(false);
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
            onClick={() => setOpen(false)}
            disabled={submitting}
            className="rounded p-1 hover:bg-[var(--muted)] disabled:opacity-40"
            title={submitting ? "正在上传，请稍候" : "关闭"}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <p className="mb-4 text-xs text-[var(--muted-foreground)]">
          支持 PDF / Word / PPT / Excel，会自动解析公式（MinerU）
          并补齐残留图片内容（视觉模型）。
          <br />
          <span className="text-sky-600 dark:text-sky-400">
            提交后会在后台运行，进度显示在页面顶部 —— 可以直接关掉这个窗口去做别的。
          </span>
        </p>

        {busyInBackground && (
          <div className="mb-3 rounded-lg border border-sky-300 bg-sky-50 p-2 text-xs text-sky-800 dark:border-sky-900 dark:bg-sky-950/30 dark:text-sky-300">
            已有导入任务正在后台运行，进度见页面顶部。重复提交会排在一起处理。
          </div>
        )}

        {/* 方式一：服务器上已有的文件夹 */}
        <div className="mb-2 flex gap-2">
          <input
            value={dir}
            onChange={(e) => setDir(e.target.value)}
            placeholder="/srv/试卷（服务器上的路径）"
            disabled={submitting}
            className="flex-1 rounded-lg border border-[var(--border)] bg-transparent px-3 py-2 text-sm outline-none focus:border-[var(--primary)]"
          />
          <button
            onClick={doScan}
            disabled={scanning || submitting}
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
            disabled={submitting}
            className="hidden"
          />
          <button
            onClick={() => dirInputRef.current?.click()}
            disabled={submitting}
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
              {!submitting && (
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
          {notice && (
            <p className="mb-3 rounded-lg border border-red-300 bg-red-50 p-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300">
              {notice}
            </p>
          )}

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
                disabled={submitting}
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
        </div>

        <div className="mt-4 flex justify-end gap-2 border-t border-[var(--border)] pt-4">
          <button
            onClick={() => setOpen(false)}
            disabled={submitting}
            className="rounded-lg px-4 py-2 text-sm hover:bg-[var(--muted)] disabled:opacity-40"
          >
            取消
          </button>
          <button
            onClick={doImport}
            disabled={submitting || (!picked.length && !scan?.docs?.length)}
            className="inline-flex items-center gap-2 rounded-lg bg-[var(--primary)] px-4 py-2 text-sm text-[var(--primary-foreground)] disabled:opacity-50"
          >
            {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
            {submitting
              ? picked.length
                ? "上传中…"
                : "提交中…"
              : "开始导入"}
          </button>
        </div>
      </div>
    </div>
  );
}
