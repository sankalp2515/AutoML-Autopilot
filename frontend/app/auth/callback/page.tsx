"use client";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/repair";
import Link from "next/link";
export default function Callback() {
  const started = useRef(false),
    [error, setError] = useState("");
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const params = new URLSearchParams(window.location.search),
      code = params.get("code"),
      state = params.get("state");
    const expected = sessionStorage.getItem("repair-oauth-state");
    sessionStorage.removeItem("repair-oauth-state");
    window.history.replaceState({}, "", window.location.pathname);
    if (!code || !state || state !== expected) {
      setError(
        "Sign-in could not be verified. Start again from Workspace settings.",
      );
      return;
    }
    void api<{ token: string; personal_workspace: string }>(
      "/auth/github/callback",
      { method: "POST", body: JSON.stringify({ code, state }) },
    )
      .then((result) => {
        sessionStorage.setItem("repair-token", result.token);
        sessionStorage.removeItem("repair-auth-error");
        localStorage.setItem("repair-workspace", result.personal_workspace);
        window.location.replace("/repair/settings");
      })
      .catch((e) => setError(e.message));
  }, []);
  return (
    <main style={{ maxWidth: 600, margin: "15vh auto", padding: 24 }}>
      <h1>{error ? "Sign-in incomplete" : "Completing GitHub sign-in"}</h1>
      <p role={error ? "alert" : "status"}>
        {error || "Confirming your account and workspace."}
      </p>
      {error && (
        <Link href="/repair/settings">Return to Workspace settings</Link>
      )}
    </main>
  );
}
