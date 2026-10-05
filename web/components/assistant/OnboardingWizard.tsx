"use client";

/**
 * 首次使用引导向导（4 步）。
 *
 * 触发条件：后端 /api/assistant/status 返回 need_onboarding = true
 * （即缺少大模型配置或 embedding 未就绪）。
 *
 * 第 3 步根据硬件自动推荐部署方式，用户可改。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, ChevronRight, Loader2 } from "lucide-react";
import { requestJson } from "@/shared/api/client";
import { isAppLanguage } from "@/i18n/languages";
import {
  resolveResponseLanguage,
  writeStoredLanguage,
  writeStoredResponseLanguage,
} from "@/context/app-shell-storage";

interface ProviderInfo {
  name: string;
  models: Record<string, string>;
  default: string;
  key_url: string;
  key_label: string;
  key_hint: string;
  base_url: string;
}

/** 云端向量服务（「全云」档用），与大模型服务商是两套独立槽位。 */
interface EmbProviderInfo {
  name: string;
  models: Record<string, string>;
  default: string;
  key_url: string;
  key_label: string;
  key_hint: string;
  base_url: string;
}

interface Status {
  need_onboarding: boolean;
  mimo_key_set: boolean;
  embed_ready: boolean;
  ollama_running: boolean;
  recommended_mode: "standard" | "full";
  is_x86: boolean;
  mem_gb: number;
  disk_free_gb: number;
  in_container?: boolean;
  /** 已有知识库数量 —— 换向量服务时要提示重建索引 */
  kb_count?: number;
}

const STEPS = ["选择大模型", "填写 Key", "部署方式", "完成"];

export default function OnboardingWizard() {
  const [visible, setVisible] = useState(false);
  const [step, setStep] = useState(0);

  const [providers, setProviders] = useState<Record<string, ProviderInfo>>({});
  const [status, setStatus] = useState<Status | null>(null);

  const [provider, setProvider] = useState("xiaomi_mimo");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [testing, setTesting] = useState(false);
  const [testMsg, setTestMsg] = useState("");

  const [mode, setMode] = useState<"cloud" | "standard" | "full">("standard");
  const [deploying, setDeploying] = useState(false);
  const [watching, setWatching] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const [cursor, setCursor] = useState(0);
  const logRef = useRef<HTMLDivElement>(null);

  // 「全云」档的向量服务配置（与大模型 Key 互相独立）
  const [embProviders, setEmbProviders] = useState<
    Record<string, EmbProviderInfo>
  >({});
  const [embProvider, setEmbProvider] = useState("zhipu");
  const [embApiKey, setEmbApiKey] = useState("");
  const [embModel, setEmbModel] = useState("");
  const [embBaseUrl, setEmbBaseUrl] = useState("");
  const [embDim, setEmbDim] = useState(0);
  const [embTesting, setEmbTesting] = useState(false);
  const [embMsg, setEmbMsg] = useState("");

  // ---------- 初始化 ----------
  useEffect(() => {
    (async () => {
      try {
        const [p, s, ep] = await Promise.all([
          requestJson<Record<string, ProviderInfo>>("/api/assistant/providers"),
          requestJson<Status>("/api/assistant/status"),
          requestJson<Record<string, EmbProviderInfo>>(
            "/api/assistant/embedding-providers",
          ),
        ]);
        setProviders(p);
        setStatus(s);
        setEmbProviders(ep);
        setMode(s.recommended_mode);
        setModel(p[p && Object.keys(p)[0]]?.default ?? "");
        const firstEmb = ep?.["zhipu"] ? "zhipu" : Object.keys(ep ?? {})[0];
        if (firstEmb) {
          setEmbProvider(firstEmb);
          setEmbModel(ep[firstEmb]?.default ?? "");
        }
        if (s.need_onboarding) setVisible(true);
      } catch {
        /* 后端未就绪时静默 */
      }
    })();
  }, []);

  // ---------- 进度轮询 ----------
  // 注意用 watching 而不是 deploying：deploy-local 接口是「启动即返回」，
  // 若用 deploying 做开关，请求一返回就会停掉轮询，日志永远是空的。
  useEffect(() => {
    if (!watching) return;
    const timer = setInterval(async () => {
      try {
        const r = await requestJson<{
          logs: { msg: string }[];
          total: number;
          task_start: number;
          running: boolean;
        }>(`/api/assistant/progress?since=${cursor}`);

        // 首次拉取时从本次任务起点开始，避免显示上一次任务的旧日志
        if (cursor === 0 && r.task_start > 0) {
          setCursor(r.task_start);
          return;
        }

        if (r.logs?.length) {
          setLogs((prev) => [...prev.slice(-300), ...r.logs.map((l) => l.msg)]);
          setCursor(r.total);
        }
        if (!r.running) setWatching(false);
      } catch {
        /* 忽略 */
      }
    }, 1500);
    return () => clearInterval(timer);
  }, [watching, cursor]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [logs]);

  const info = providers[provider];

  const onProviderChange = useCallback(
    (k: string) => {
      setProvider(k);
      const p = providers[k];
      if (p) setModel(p.default || Object.keys(p.models)[0] || "");
      setTestMsg("");
    },
    [providers],
  );

  // ---------- 操作 ----------
  const doTest = async () => {
    if (!apiKey.trim()) {
      setTestMsg("请先填写 API Key");
      return;
    }
    setTesting(true);
    setTestMsg("");
    try {
      const r = await requestJson<{
        ok: boolean;
        msg: string;
        test?: { ok: boolean; msg: string };
      }>("/api/assistant/save-llm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          api_key: apiKey.trim(),
          provider,
          model,
          base_url: baseUrl,
        }),
      });
      setTestMsg(r.ok ? `✓ ${r.test?.msg ?? "已保存"}` : `✗ ${r.msg}`);
      if (r.ok) setStep(2);
    } catch (e) {
      setTestMsg(`✗ ${e}`);
    } finally {
      setTesting(false);
    }
  };

  const doTestEmbedding = async () => {
    if (!embApiKey.trim()) {
      setEmbMsg("请先填写向量服务的 API Key");
      return;
    }
    setEmbTesting(true);
    setEmbMsg("");
    try {
      const r = await requestJson<{
        ok: boolean;
        msg: string;
        dimension?: number;
      }>("/api/assistant/test-embedding", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          api_key: embApiKey.trim(),
          provider: embProvider,
          model: embModel,
          base_url: embBaseUrl,
        }),
      });
      setEmbMsg(r.ok ? `✓ ${r.msg}` : `✗ ${r.msg}`);
      // 维度由服务端实测反推，不手填 —— 写错会让检索静默失效
      if (r.ok && r.dimension) setEmbDim(r.dimension);
    } catch (e) {
      setEmbMsg(`✗ ${e}`);
    } finally {
      setEmbTesting(false);
    }
  };

  const doDeploy = async () => {
    if (mode === "cloud" && (!embApiKey.trim() || embDim <= 0)) {
      setEmbMsg("请先填写向量服务的 Key 并点「测试连接」");
      return;
    }
    setDeploying(true);
    try {
      // 只启动后台任务，不等它跑完 —— 立即进入下一步
      await requestJson("/api/assistant/deploy-local", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mode,
          embedding:
            mode === "cloud"
              ? {
                  provider: embProvider,
                  api_key: embApiKey.trim(),
                  model: embModel,
                  base_url: embBaseUrl,
                  dimension: embDim,
                }
              : null,
        }),
      });
      setWatching(true);
      setStep(3);
    } catch (e) {
      setLogs((prev) => [...prev, `启动失败: ${e}`]);
    } finally {
      setDeploying(false);
    }
  };

  const finish = async () => {
    localStorage.setItem("deeptutor-onboarded", "1");
    // 引导完成后，把后端的界面设置（语言/主题）同步到浏览器缓存。
    // AppShellContext 只在本地无缓存时才去读后端，一旦浏览器里存过
    // 一个旧值（比如早期默认的 en），后端的 zh 就永远不会生效 —— 这里
    // 显式写入，并用官方 helper 触发同一套 storage 事件。
    try {
      const ui = await requestJson<{
        language?: unknown;
        response_language?: unknown;
      }>("/api/settings/ui");
      if (isAppLanguage(ui?.language)) {
        writeStoredLanguage(ui.language);
        writeStoredResponseLanguage(
          resolveResponseLanguage(
            typeof ui.response_language === "string"
              ? ui.response_language
              : null,
            ui.language,
          ),
        );
      }
    } catch {
      /* 后端不可达时保持现状 */
    }
    setVisible(false);
    window.location.reload();
  };

  if (!visible) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 p-4">
      <div className="w-full max-w-xl rounded-2xl bg-[var(--background)] p-8 shadow-2xl">
        {/* 步骤条 */}
        <div className="mb-8 flex items-center gap-2">
          {STEPS.map((label, i) => (
            <div key={label} className="flex items-center gap-2">
              <div
                className={`flex h-7 w-7 items-center justify-center rounded-full text-xs ${
                  i < step
                    ? "bg-green-500 text-white"
                    : i === step
                      ? "bg-[var(--primary)] text-[var(--primary-foreground)]"
                      : "bg-[var(--muted)] text-[var(--muted-foreground)]"
                }`}
              >
                {i < step ? <Check className="h-3.5 w-3.5" /> : i + 1}
              </div>
              <span
                className={`text-xs ${i === step ? "font-medium" : "text-[var(--muted-foreground)]"}`}
              >
                {label}
              </span>
              {i < STEPS.length - 1 && (
                <ChevronRight className="h-3 w-3 text-[var(--muted-foreground)]" />
              )}
            </div>
          ))}
        </div>

        {/* 第 1 步 */}
        {step === 0 && (
          <div>
            <h2 className="mb-2 text-lg font-medium">欢迎使用 · 只需 3 步</h2>
            <p className="mb-6 text-sm text-[var(--muted-foreground)]">
              先选一个提供大模型的服务商。它负责出题和理解试卷里的图片。
            </p>
            <div className="space-y-3">
              {Object.entries(providers).map(([k, p]) => (
                <button
                  key={k}
                  onClick={() => onProviderChange(k)}
                  className={`w-full rounded-xl border p-4 text-left transition ${
                    provider === k
                      ? "border-[var(--primary)] bg-[var(--muted)]"
                      : "border-[var(--border)] hover:bg-[var(--muted)]"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium">{p.name}</span>
                    {provider === k && (
                      <Check className="h-4 w-4 text-[var(--primary)]" />
                    )}
                  </div>
                  {p.key_hint && (
                    <p className="mt-1 text-xs text-[var(--muted-foreground)]">
                      {p.key_hint}
                    </p>
                  )}
                </button>
              ))}
            </div>
            <div className="mt-6 flex justify-end">
              <button
                onClick={() => setStep(1)}
                className="rounded-lg bg-[var(--primary)] px-5 py-2 text-sm text-[var(--primary-foreground)]"
              >
                下一步
              </button>
            </div>
          </div>
        )}

        {/* 第 2 步 */}
        {step === 1 && (
          <div>
            <h2 className="mb-2 text-lg font-medium">填写 API Key</h2>
            <p className="mb-6 text-sm text-[var(--muted-foreground)]">
              这是唯一需要你准备的东西。填完会自动测试连接。
            </p>

            <label className="mb-1 block text-xs text-[var(--muted-foreground)]">
              API Key
            </label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="sk-..."
              className="mb-3 w-full rounded-lg border border-[var(--border)] bg-transparent px-3 py-2 text-sm outline-none focus:border-[var(--primary)]"
            />

            {info && Object.keys(info.models).length > 0 && (
              <>
                <label className="mb-1 block text-xs text-[var(--muted-foreground)]">
                  模型
                </label>
                <select
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  className="mb-3 w-full rounded-lg border border-[var(--border)] bg-transparent px-3 py-2 text-sm"
                >
                  {Object.entries(info.models).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </select>
              </>
            )}

            {info && !Object.keys(info.models).length && (
              <>
                <label className="mb-1 block text-xs text-[var(--muted-foreground)]">
                  Base URL
                </label>
                <input
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder="https://your-api.com/v1"
                  className="mb-3 w-full rounded-lg border border-[var(--border)] bg-transparent px-3 py-2 text-sm"
                />
                <label className="mb-1 block text-xs text-[var(--muted-foreground)]">
                  模型名
                </label>
                <input
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  className="mb-3 w-full rounded-lg border border-[var(--border)] bg-transparent px-3 py-2 text-sm"
                />
              </>
            )}

            {info?.key_url && (
              <a
                href={info.key_url}
                target="_blank"
                rel="noreferrer"
                className="mb-4 inline-block rounded-lg bg-green-600 px-4 py-2 text-xs text-white hover:bg-green-700"
              >
                {info.key_label || "去获取 API Key"} →
              </a>
            )}

            {testMsg && (
              <p
                className={`mb-4 text-sm ${testMsg.startsWith("✓") ? "text-green-600" : "text-red-500"}`}
              >
                {testMsg}
              </p>
            )}

            <div className="flex justify-between">
              <button
                onClick={() => setStep(0)}
                className="rounded-lg px-4 py-2 text-sm hover:bg-[var(--muted)]"
              >
                上一步
              </button>
              <button
                onClick={doTest}
                disabled={testing}
                className="inline-flex items-center gap-2 rounded-lg bg-[var(--primary)] px-5 py-2 text-sm text-[var(--primary-foreground)] disabled:opacity-50"
              >
                {testing && <Loader2 className="h-4 w-4 animate-spin" />}
                {testing ? "测试中…" : "保存并测试"}
              </button>
            </div>
          </div>
        )}

        {/* 第 3 步 */}
        {step === 2 && (
          <div>
            <h2 className="mb-2 text-lg font-medium">选择部署方式</h2>
            <p className="mb-6 text-sm text-[var(--muted-foreground)]">
              {status?.recommended_mode === mode
                ? "已根据你的机器配置推荐（可更改）"
                : "可更改"}
            </p>

            <div className="space-y-3">
              {/* 全云 */}
              <button
                onClick={() => setMode("cloud")}
                className={`w-full rounded-xl border p-4 text-left ${
                  mode === "cloud"
                    ? "border-[var(--primary)] bg-[var(--muted)]"
                    : "border-[var(--border)]"
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium">全云</span>
                  <span className="rounded bg-blue-100 px-2 py-0.5 text-[10px] text-blue-700">
                    不下载任何模型
                  </span>
                </div>
                <p className="mt-1 text-xs text-[var(--muted-foreground)]">
                  下载 0 GB
                </p>
                <p className="mt-1 text-[11px] text-[var(--muted-foreground)]">
                  <b>试卷解析：云端视觉模型</b>　·　
                  <b>知识库检索：云端向量服务</b>
                </p>
                <p className="mt-1 text-[11px] text-[var(--muted-foreground)]">
                  需要另外填一个向量服务的 Key（可与上面的大模型不是同一家）
                </p>
              </button>

              {/* 全云选中时展开的向量服务子表单 */}
              {mode === "cloud" && (
                <div className="rounded-xl border border-[var(--primary)] bg-[var(--muted)] p-4">
                  <p className="mb-3 text-xs font-medium">
                    向量服务（知识库检索用）
                  </p>

                  <select
                    value={embProvider}
                    onChange={(e) => {
                      const k = e.target.value;
                      setEmbProvider(k);
                      setEmbModel(embProviders[k]?.default ?? "");
                      setEmbDim(0);
                      setEmbMsg("");
                    }}
                    className="mb-2 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm"
                  >
                    {Object.entries(embProviders).map(([k, v]) => (
                      <option key={k} value={k}>
                        {v.name}
                      </option>
                    ))}
                  </select>

                  {embProviders[embProvider]?.models &&
                    Object.keys(embProviders[embProvider].models).length >
                      0 && (
                      <select
                        value={embModel}
                        onChange={(e) => setEmbModel(e.target.value)}
                        className="mb-2 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm"
                      >
                        {Object.entries(
                          embProviders[embProvider].models,
                        ).map(([k, v]) => (
                          <option key={k} value={k}>
                            {v}
                          </option>
                        ))}
                      </select>
                    )}

                  {embProviders[embProvider]?.key_url && (
                    <a
                      href={embProviders[embProvider].key_url}
                      target="_blank"
                      rel="noreferrer"
                      className="mb-2 block text-[11px] text-[var(--primary)] underline"
                    >
                      {embProviders[embProvider].key_label ||
                        "去获取 API Key"}{" "}
                      ↗
                    </a>
                  )}

                  <input
                    type="password"
                    value={embApiKey}
                    onChange={(e) => {
                      setEmbApiKey(e.target.value);
                      setEmbDim(0);
                    }}
                    placeholder="粘贴向量服务的 API Key"
                    className="mb-2 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm"
                  />

                  {embProviders[embProvider] &&
                    !Object.keys(embProviders[embProvider].models).length && (
                      <>
                        <input
                          value={embBaseUrl}
                          onChange={(e) => setEmbBaseUrl(e.target.value)}
                          placeholder="base_url，如 https://api.example.com/v1"
                          className="mb-2 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm"
                        />
                        <input
                          value={embModel}
                          onChange={(e) => setEmbModel(e.target.value)}
                          placeholder="向量模型名"
                          className="mb-2 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm"
                        />
                      </>
                    )}

                  <button
                    onClick={doTestEmbedding}
                    disabled={embTesting}
                    className="inline-flex items-center gap-2 rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-1.5 text-xs disabled:opacity-50"
                  >
                    {embTesting && (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    )}
                    测试连接
                  </button>

                  {embDim > 0 && (
                    <span className="ml-2 text-[11px] text-green-600">
                      向量维度 {embDim}
                    </span>
                  )}

                  {embMsg && (
                    <p className="mt-2 text-[11px] text-[var(--muted-foreground)]">
                      {embMsg}
                    </p>
                  )}

                  <p className="mt-2 text-[11px] leading-relaxed text-[var(--muted-foreground)]">
                    维度由测试时实测得出，不用手填。
                  </p>

                  {/* 换向量服务会让已建索引失配，必须提前说清楚 */}
                  {(status?.kb_count ?? 0) > 0 && (
                    <p className="mt-2 text-[11px] leading-relaxed text-amber-600">
                      ⚠ 你已有 {status?.kb_count} 个知识库。向量服务换了以后，
                      旧索引不再匹配，需要重新导入试卷才能正常检索。
                    </p>
                  )}
                </div>
              )}

              <button
                onClick={() => setMode("standard")}
                className={`w-full rounded-xl border p-4 text-left ${
                  mode === "standard"
                    ? "border-[var(--primary)] bg-[var(--muted)]"
                    : "border-[var(--border)]"
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium">标准部署</span>
                  {status?.recommended_mode === "standard" && (
                    <span className="rounded bg-green-100 px-2 py-0.5 text-[10px] text-green-700">
                      推荐
                    </span>
                  )}
                </div>
                <p className="mt-1 text-xs text-[var(--muted-foreground)]">
                  下载约 1.2GB（本地向量模型 bge-m3）
                </p>
                <p className="mt-1 text-[11px] text-[var(--muted-foreground)]">
                  <b>试卷解析：云端视觉模型</b>　·　支持 PDF / Word / PPT / Excel
                </p>
              </button>

              <button
                onClick={() => setMode("full")}
                className={`w-full rounded-xl border p-4 text-left ${
                  mode === "full"
                    ? "border-[var(--primary)] bg-[var(--muted)]"
                    : "border-[var(--border)]"
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium">全本地部署</span>
                  {status?.recommended_mode === "full" && (
                    <span className="rounded bg-green-100 px-2 py-0.5 text-[10px] text-green-700">
                      推荐
                    </span>
                  )}
                </div>
                <p className="mt-1 text-xs text-[var(--muted-foreground)]">
                  下载约 4GB（本地向量模型 + MinerU 解析模型）
                </p>
                <p className="mt-1 text-[11px] text-[var(--muted-foreground)]">
                  <b>试卷解析：本地 MinerU</b>　·　公式识别更完整
                </p>
                <p className="mt-1 text-[11px] text-[var(--muted-foreground)]">
                  残留图片仍会用云端模型补齐（用上面填的 Key）
                </p>
                {status && (!status.is_x86 || status.mem_gb < 8) && (
                  <p className="mt-1 text-xs text-amber-600">
                    ⚠ 你的机器配置可能不足（需 x86 + 8GB 内存）
                  </p>
                )}
              </button>
            </div>

            {/* 模型下载位置说明（全云不下载，无需展示） */}
            {mode !== "cloud" && (
              <div className="mt-4 rounded-lg border border-[var(--border)] bg-[var(--muted)] p-3">
                <p className="mb-1 text-xs font-medium">模型会下载到哪里？</p>
                <p className="text-[11px] leading-relaxed text-[var(--muted-foreground)]">
                  程序会自动下载到 <code className="rounded bg-black/10 px-1">
                  {status?.in_container ? "/app/models/" : "<程序目录>/models/"}</code>，
                  分 ollama（向量模型）与 mineru（解析模型）两个子目录。
                  <b>该目录可整体拷贝到其他机器</b>，换设备时不用重新下载。
                </p>
              </div>
            )}

            {logs.length > 0 && (
              <div
                ref={logRef}
                className="mt-4 max-h-32 overflow-y-auto rounded-lg bg-black/80 p-3 font-mono text-xs leading-relaxed text-green-300"
              >
                {logs.map((l, i) => (
                  <div key={i}>{l}</div>
                ))}
              </div>
            )}

            <div className="mt-6 flex justify-between">
              <button
                onClick={() => setStep(1)}
                disabled={deploying}
                className="rounded-lg px-4 py-2 text-sm hover:bg-[var(--muted)] disabled:opacity-40"
              >
                上一步
              </button>
              <button
                onClick={doDeploy}
                disabled={deploying}
                className="inline-flex items-center gap-2 rounded-lg bg-[var(--primary)] px-5 py-2 text-sm text-[var(--primary-foreground)] disabled:opacity-50"
              >
                {deploying && <Loader2 className="h-4 w-4 animate-spin" />}
                {deploying
                  ? "处理中…"
                  : mode === "cloud"
                    ? "完成配置并进入"
                    : "开始部署并进入"}
              </button>
            </div>
            <p className="mt-2 text-center text-[11px] text-[var(--muted-foreground)]">
              {mode === "cloud"
                ? "全云模式不下载任何模型，配置立即生效"
                : "模型会在后台下载，页面顶部显示进度，不用在这里等"}
            </p>
          </div>
        )}

        {/* 第 4 步 */}
        {step === 3 && (
          <div className="text-center">
            <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-green-100">
              <Check className="h-7 w-7 text-green-600" />
            </div>
            <h2 className="mb-2 text-lg font-medium">配置完成</h2>
            <p className="mb-3 text-sm text-[var(--muted-foreground)]">
              模型正在后台下载（页面顶部会显示进度）。
              <br />
              下载期间你可以先熟悉界面，完成后会自动启用。
            </p>
            <p className="mb-6 text-xs text-[var(--muted-foreground)]">
              接下来：进「学习空间 → 知识中心」，
              在知识库里点「增强导入」导入试卷。
            </p>
            <button
              onClick={finish}
              className="rounded-lg bg-[var(--primary)] px-6 py-2 text-sm text-[var(--primary-foreground)]"
            >
              开始使用
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
