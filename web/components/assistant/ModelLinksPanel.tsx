"use client";

/**
 * 手动下载清单面板。
 *
 * 给想用迅雷 / IDM 下载模型的用户：列出每个文件的**直链**和它该放的
 * **目标目录**。程序自己下也能跑，但国内网络下 modelscope 可能很慢，
 * 有个直链能省不少事。
 *
 * 直链是 modelscope 的 API 地址，会 302 到 CDN，带 Content-Length
 * 且支持 Range，多线程下载工具可直接用。
 *
 * 注意：直链下载**会丢目录结构**（CDN 只认 filename），所以每个文件都
 * 标出它在仓库里的相对路径，用户下完要按路径归位。
 */

import { useEffect, useState } from "react";
import { Copy, Loader2 } from "lucide-react";
import { requestJson } from "@/shared/api/client";

interface ManifestFile {
  path: string;
  size_mb: number;
  url: string;
}

interface ManifestGroup {
  id: string;
  title: string;
  desc: string;
  repo: string;
  page: string;
  target_host?: string;
  target_container?: string;
  size_mb?: number;
  files: ManifestFile[];
  error?: string;
}

interface EmbedNote {
  title: string;
  desc: string;
  gguf_repo: string;
  gguf_file: string;
  page: string;
  url: string;
}

interface Manifest {
  ok: boolean;
  total_mb: number;
  groups: ManifestGroup[];
  embed_note: EmbedNote;
}

export default function ModelLinksPanel() {
  const [data, setData] = useState<Manifest | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [copied, setCopied] = useState("");

  // 挂载即加载 —— 横幅出现时用户最需要的就是这些链接，不该再点一次
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const d = await requestJson<Manifest>("/api/assistant/model-links");
        if (!cancelled) setData(d);
      } catch {
        if (!cancelled) setFailed(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const copy = async (text: string, key: string) => {
    try {
      // 非安全上下文（http + 非 localhost）下 clipboard API 不可用，退回 execCommand
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setCopied(key);
      setTimeout(() => setCopied(""), 1500);
    } catch {
      /* 复制失败时用户可手动选中 */
    }
  };

  return (
    <div className="mt-2 rounded border border-white/20 bg-black/40 p-2 text-[11px]">
      {loading && (
        <p className="flex items-center gap-1.5 text-green-300">
          <Loader2 className="h-3 w-3 animate-spin" />
          正在获取下载链接...
        </p>
      )}

      {failed && (
        <p className="text-amber-300">
          获取下载链接失败（后端未响应）。稍后刷新页面即可重试。
        </p>
      )}

      {data && (
        <div className="pt-1">
          <p className="mb-2 text-green-200">
            <b>下载太慢？</b>可以用下载工具（迅雷 / IDM）复制下面的链接下载，
            然后放到对应的目录里。合计约 <b>{data.total_mb} MB</b>。
            直链下载会丢目录结构，请按每个文件标注的相对路径放好。
          </p>

          {data.groups.map((g) => (
            <div key={g.id} className="mb-3 rounded bg-black/30 p-2">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="font-medium text-green-200">{g.title}</span>
                <span className="text-green-400/70">{g.size_mb} MB</span>
                <span className="text-green-400/60">{g.desc}</span>
              </div>

              {g.error ? (
                <p className="mt-1 text-red-300">
                  清单获取失败：{g.error}
                  <br />
                  可直接访问 {g.page}
                </p>
              ) : (
                <>
                  <p className="mt-1 text-green-300">
                    放到：<code className="rounded bg-black/50 px-1">
                      {g.target_host}
                    </code>
                    <span className="text-green-400/60">
                      （容器内 {g.target_container}）
                    </span>
                  </p>

                  <div className="mt-1 flex gap-2">
                    <button
                      onClick={() =>
                        copy(
                          g.files.map((f) => f.url).join("\n"),
                          `all-${g.id}`,
                        )
                      }
                      className="rounded border border-white/20 px-2 py-0.5 text-green-300 hover:bg-white/10"
                    >
                      {copied === `all-${g.id}`
                        ? "✓ 已复制"
                        : `复制全部 ${g.files.length} 个链接`}
                    </button>
                    <button
                      onClick={() =>
                        setExpanded((v) => ({ ...v, [g.id]: !v[g.id] }))
                      }
                      className="rounded border border-white/20 px-2 py-0.5 text-green-300 hover:bg-white/10"
                    >
                      {expanded[g.id] ? "收起文件列表" : "展开文件列表"}
                    </button>
                  </div>

                  {expanded[g.id] && (
                    <div className="mt-1 max-h-48 overflow-y-auto">
                      {g.files.map((f) => (
                        <div
                          key={f.path}
                          className="flex items-center gap-2 border-b border-white/10 py-0.5 last:border-0"
                        >
                          <span className="w-16 shrink-0 text-right text-green-400/70">
                            {f.size_mb} MB
                          </span>
                          <span
                            className="flex-1 truncate text-green-200"
                            title={f.path}
                          >
                            {f.path}
                          </span>
                          <button
                            onClick={() => copy(f.url, f.path)}
                            className="shrink-0 rounded border border-white/20 px-1.5 text-green-300 hover:bg-white/10"
                            title={f.url}
                          >
                            {copied === f.path ? "✓" : <Copy className="h-3 w-3" />}
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          ))}

          <div className="rounded bg-black/30 p-2">
            <div className="font-medium text-green-200">
              {data.embed_note.title}
            </div>
            <p className="mt-1 text-green-400/80">{data.embed_note.desc}</p>
            <div className="mt-1 flex items-center gap-2">
              <code className="flex-1 truncate rounded bg-black/50 px-1">
                {data.embed_note.gguf_file}
              </code>
              <button
                onClick={() => copy(data.embed_note.url, "embed")}
                className="shrink-0 rounded border border-white/20 px-1.5 text-green-300 hover:bg-white/10"
              >
                {copied === "embed" ? "✓ 已复制" : "复制 GGUF 链接"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
