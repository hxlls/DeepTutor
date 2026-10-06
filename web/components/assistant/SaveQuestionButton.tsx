"use client";

/**
 * 保存题目按钮 —— 挂在出题卡片的工具栏上。
 *
 * 原生逻辑：答题提交后才出现「收藏 / 分类」按钮。
 * 本组件补充：任何时候都能把这道题存进题库或错题本。
 *
 * 题库与错题本是同一张表（notebook_entries），靠 is_correct 区分。
 */

import { useState } from "react";
import { Check, Loader2, Save } from "lucide-react";
import { requestJson } from "@/shared/api/client";

interface Props {
  question: string;
  questionType?: string;
  options?: Record<string, string>;
  correctAnswer?: string;
  explanation?: string;
  userAnswer?: string;
  category?: string;
}

export default function SaveQuestionButton({
  question,
  questionType = "",
  options = {},
  correctAnswer = "",
  explanation = "",
  userAnswer = "",
  category = "",
}: Props) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<"bank" | "mistakes" | null>(null);

  const save = async (target: "bank" | "mistakes") => {
    setBusy(true);
    try {
      const r = await requestJson<{ ok: boolean; msg: string }>(
        "/api/assistant/save-question",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            question,
            question_type: questionType,
            options,
            correct_answer: correctAnswer,
            explanation,
            user_answer: userAnswer,
            category,
            target,
          }),
        },
      );
      if (r.ok) {
        setDone(target);
        setTimeout(() => setDone(null), 2000);
      } else {
        alert(r.msg);
      }
    } catch (e) {
      alert(`保存失败：${e}`);
    } finally {
      setBusy(false);
      setOpen(false);
    }
  };

  if (done) {
    return (
      <span className="inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-xs text-green-600">
        <Check size={14} />
        已存入{done === "mistakes" ? "错题本" : "题库"}
      </span>
    );
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={busy}
        title="保存这道题"
        aria-label="保存这道题"
        className="rounded-lg p-1.5 text-[var(--muted-foreground)] transition-all hover:text-[var(--primary)] disabled:opacity-30"
      >
        {busy ? (
          <Loader2 size={18} className="animate-spin" />
        ) : (
          <Save size={18} strokeWidth={1.8} />
        )}
      </button>

      {open && (
        <>
          {/* 点击遮罩关闭 */}
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-50 mt-1 w-40 overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--background)] shadow-lg">
            <button
              type="button"
              onClick={() => void save("bank")}
              className="block w-full px-3 py-2 text-left text-xs hover:bg-[var(--muted)]"
            >
              存入题库
            </button>
            <button
              type="button"
              onClick={() => void save("mistakes")}
              className="block w-full border-t border-[var(--border)] px-3 py-2 text-left text-xs hover:bg-[var(--muted)]"
            >
              存入错题本
            </button>
          </div>
        </>
      )}
    </div>
  );
}
