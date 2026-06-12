import { useState } from "react";
import { auth } from "@/lib/api";

export function LoginPage({ onSuccess }: { onSuccess: () => void }) {
  const [email, setEmail] = useState("admin");
  const [password, setPassword] = useState("admin123");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setErr("");
    setBusy(true);
    try {
      await auth.login(email.trim(), password);
      onSuccess();
    } catch {
      setErr("登录失败,请检查邮箱与密码");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-screen items-center justify-center bg-canvas">
      <div className="w-[360px] rounded-2xl border border-line bg-surface p-7 shadow-sm">
        <div className="mb-5 text-[15px] font-semibold text-ink">登录以使用 PixelFlow</div>

        <label className="mb-1 block text-[12px] font-medium text-ink-soft">账号</label>
        <input
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="mb-3 w-full rounded-lg border border-line bg-canvas px-3 py-2 text-[14px] outline-none focus:border-accent/40"
        />
        <label className="mb-1 block text-[12px] font-medium text-ink-soft">密码</label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          className="mb-4 w-full rounded-lg border border-line bg-canvas px-3 py-2 text-[14px] outline-none focus:border-accent/40"
        />

        {err && <div className="mb-3 text-[13px] text-rose-500">{err}</div>}

        <button
          onClick={submit}
          disabled={busy || !password}
          className="w-full rounded-xl bg-brand py-2.5 text-[14px] font-medium text-white transition-opacity disabled:opacity-40"
        >
          {busy ? "登录中…" : "登录"}
        </button>
      </div>
    </div>
  );
}
