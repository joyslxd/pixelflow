import { useEffect, useState, type ReactNode } from "react";
import { auth } from "@/lib/api";
import { LoginPage } from "./LoginPage";

export function AuthGate({ children }: { children: ReactNode }) {
  const [authed, setAuthed] = useState<boolean | null>(null);

  const check = () => auth.me().then((r) => setAuthed(r.authenticated)).catch(() => setAuthed(false));
  useEffect(() => {
    void check();
  }, []);

  if (authed === null) {
    return (
      <div className="flex h-screen items-center justify-center bg-canvas text-[13px] text-ink-soft">
        加载中…
      </div>
    );
  }
  if (!authed) return <LoginPage onSuccess={() => setAuthed(true)} />;
  return <>{children}</>;
}
