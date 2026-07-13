import { FormEvent, useEffect, useState } from "react";
import { ArrowLeft, CheckCircle2, KeyRound, ShieldCheck, Trash2 } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { api } from "@/lib/api";
import { AUTHORIZATION_STORAGE_KEY, clearSavedAuthorization, getBrowserAuthorization, saveAuthorization } from "@/lib/authStorage";

type CheckState =
  | { type: "idle"; message: string }
  | { type: "success"; message: string }
  | { type: "error"; message: string };

export function AuthTokenPage() {
  const navigate = useNavigate();
  const [authorization, setAuthorization] = useState("");
  const [checking, setChecking] = useState(false);
  const [state, setState] = useState<CheckState>({
    type: "idle",
    message: "粘贴 content-app 登录 token 后保存，本机前端后续请求会自动携带 Authorization。",
  });

  useEffect(() => {
    setAuthorization(getBrowserAuthorization());
  }, []);

  const saveCurrentAuthorization = () => {
    const saved = saveAuthorization(authorization);
    setAuthorization(saved);
    setState({ type: "success", message: `已保存到 localStorage.${AUTHORIZATION_STORAGE_KEY}` });
    return saved;
  };

  const verifyCurrentAuthorization = async () => {
    setChecking(true);
    try {
      saveCurrentAuthorization();
      const user = await api.getCurrentUser();
      setState({ type: "success", message: `校验通过，当前 content-app 用户：${user.username || user.id}` });
    } catch (err) {
      setState({ type: "error", message: err instanceof Error ? err.message : String(err) });
    } finally {
      setChecking(false);
    }
  };

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void verifyCurrentAuthorization();
  };

  const clearAuthorization = () => {
    clearSavedAuthorization();
    setAuthorization("");
    setState({ type: "idle", message: "已清除本机保存的 Authorization。" });
  };

  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto px-6 py-5">
      <div className="mx-auto flex w-full max-w-4xl flex-1 flex-col">
        <button
          type="button"
          onClick={() => navigate("/")}
          className="mb-5 inline-flex w-fit items-center gap-2 rounded-lg border border-line bg-surface px-3 py-2 text-[13px] font-medium text-ink-soft transition-colors hover:border-accent/40 hover:text-accent"
        >
          <ArrowLeft size={16} />
          返回工作台
        </button>

        <section className="rounded-2xl border border-line bg-surface p-6 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-4 border-b border-line pb-5">
            <div>
              <div className="mb-2 inline-flex items-center gap-2 rounded-full bg-accent-soft px-3 py-1 text-[12px] font-semibold text-accent">
                <KeyRound size={14} />
                本地调试 Authorization
              </div>
              <h1 className="text-[24px] font-semibold tracking-normal text-ink">
                设置 content-app 登录 token
              </h1>
              <p className="mt-2 max-w-2xl text-[14px] leading-6 text-ink-soft">
                这里保存的值只用于你本机浏览器调试。正式联动时，content-app 可以直接注入
                <span className="mx-1 font-mono text-[13px] text-ink">window.__CONTENT_APP_AUTHORIZATION__</span>
                或写入同名存储。
              </p>
            </div>
            <div className="flex items-center gap-2 rounded-lg border border-line bg-canvas px-3 py-2 text-[12px] text-ink-soft">
              <ShieldCheck size={15} />
              存储键：<span className="font-mono text-ink">{AUTHORIZATION_STORAGE_KEY}</span>
            </div>
          </div>

          <form onSubmit={onSubmit} className="mt-5 space-y-4">
            <label className="block">
              <span className="mb-2 block text-[13px] font-medium text-ink">Authorization 或原始 JWT</span>
              <textarea
                value={authorization}
                onChange={(event) => setAuthorization(event.target.value)}
                spellCheck={false}
                placeholder="Bearer eyJhbGciOiJIUzI1NiJ9..."
                className="min-h-[170px] w-full resize-y rounded-xl border border-line bg-canvas px-4 py-3 font-mono text-[13px] leading-6 text-ink outline-none transition-colors placeholder:text-ink-soft/55 focus:border-accent"
              />
            </label>

            <div
              className={
                state.type === "success"
                  ? "rounded-xl border border-emerald/20 bg-emerald/5 px-4 py-3 text-[13px] leading-6 text-emerald"
                  : state.type === "error"
                    ? "rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-[13px] leading-6 text-red-700"
                    : "rounded-xl border border-line bg-canvas px-4 py-3 text-[13px] leading-6 text-ink-soft"
              }
            >
              {state.message}
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <button
                type="submit"
                disabled={checking}
                className="inline-flex h-10 items-center gap-2 rounded-lg bg-brand px-4 text-[14px] font-medium text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
              >
                <CheckCircle2 size={16} />
                {checking ? "验证中" : "保存并验证"}
              </button>
              <button
                type="button"
                onClick={() => {
                  try {
                    saveCurrentAuthorization();
                  } catch (err) {
                    setState({ type: "error", message: err instanceof Error ? err.message : String(err) });
                  }
                }}
                className="inline-flex h-10 items-center gap-2 rounded-lg border border-line bg-surface px-4 text-[14px] font-medium text-ink transition-colors hover:border-accent/40 hover:text-accent"
              >
                <KeyRound size={16} />
                只保存
              </button>
              <button
                type="button"
                onClick={clearAuthorization}
                className="inline-flex h-10 items-center gap-2 rounded-lg border border-line bg-surface px-4 text-[14px] font-medium text-ink-soft transition-colors hover:border-red-200 hover:text-red-600"
              >
                <Trash2 size={16} />
                清除
              </button>
            </div>
          </form>
        </section>
      </div>
    </div>
  );
}
