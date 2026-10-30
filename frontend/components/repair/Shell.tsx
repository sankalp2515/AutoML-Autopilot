"use client";
import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { useIdentity } from "@/lib/repair-identity";
import { Status, useResource } from "@/lib/repair";
import { Button, Icon, Mark, Modal } from "./ui";

const navigation = [
  ["/repair", "Home", "home"],
  ["/repair/new", "New repair", "plus"],
  ["/repair/runs", "Runs", "clock"],
  ["/repair/benchmark", "Benchmark", "chart"],
  ["/repair/how-it-works", "How it works", "shield"],
  ["/repair/docs", "Docs", "book"],
];
export default function RepairShell({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const identity = useIdentity();
  const [dark, setDark] = useState(false),
    [menu, setMenu] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const status = useResource<Status>("/status", 30000);
  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
  }, []);
  useEffect(() => setMenu(false), [pathname]);
  useEffect(() => {
    const node = root.current;
    if (!node) return;
    const update = () =>
      node.querySelectorAll<HTMLElement>('[role="tab"]').forEach((tab) => {
        tab.tabIndex = tab.getAttribute("aria-selected") === "true" ? 0 : -1;
      });
    update();
    const observer = new MutationObserver(update);
    observer.observe(node, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["aria-selected"],
    });
    return () => observer.disconnect();
  }, []);
  const title =
    navigation.find(([href]) => href === pathname)?.[1] ||
    (pathname.includes("/replay/")
      ? "Recorded repair"
      : pathname.includes("/tasks/")
        ? "Repair workspace"
        : "Workspace settings");
  const nav = (
    <nav aria-label="Main navigation">
      {navigation.map(([href, text, icon]) => (
        <Link
          key={href}
          href={href}
          className={`r-nav-link ${pathname === href || (href === "/repair" && pathname === "/") ? "active" : ""}`}
          aria-current={pathname === href ? "page" : undefined}
        >
          <Icon name={icon} />
          {text}
          {href === "/repair/new" && <span className="r-nav-plus">+</span>}
        </Link>
      ))}
    </nav>
  );
  function toggle() {
    const next = !dark;
    document.documentElement.classList.toggle("dark", next);
    document.documentElement.classList.toggle("light", !next);
    localStorage.setItem("atelier-theme", next ? "dark" : "light");
    setDark(next);
  }
  const connected = status.data;
  if (pathname === "/" || pathname === "/repair")
    return (
      <div className="repair r-public" ref={root}>
        <a href="#repair-content" className="r-skip">
          Skip to main content
        </a>
        <header className="r-public-header">
          <Link href="/repair" className="r-brand">
            <Mark />
            <span>
              AutoML<span>Autopilot</span>
            </span>
          </Link>
          <nav aria-label="Main navigation">
            <a href="#evidence">Evidence</a>
            <a href="#workflow">How it works</a>
            <a href="#access">Access</a>
            <Link href="/repair/docs">Docs</Link>
          </nav>
          <div className="r-public-header-actions">
            <Button
              kind="quiet"
              icon={dark ? "sun" : "moon"}
              aria-label={`Switch to ${dark ? "light" : "dark"} theme`}
              onClick={toggle}
            />
            <Link href="/repair/new" className="r-button r-button-primary">
              Open workspace
            </Link>
          </div>
        </header>
        <main id="repair-content" tabIndex={-1}>
          {children}
        </main>
        <footer className="r-public-footer">
          <div>
            <strong>AutoML Autopilot</strong>
            <p>Code correctness. ML integrity. Evidence for both.</p>
          </div>
          <nav aria-label="Footer navigation">
            <Link href="/repair/benchmark">Benchmark</Link>
            <Link href="/repair/docs?section=privacy">Privacy & retention</Link>
            <a href="https://github.com/sankalp2515/AutoML-Autopilot">
              Source · GPL-3.0
            </a>
            <Link href="/autopilot">Data incident mode</Link>
          </nav>
          <Link href="/repair/how-it-works#runtime" className="r-runtime">
            <span
              className={`r-status-dot ${connected?.successful_calls_7d ? "r-dot-success" : ""}`}
            />
            {status.loading
              ? "Checking runtime"
              : status.error
                ? "Service unavailable"
                : connected?.live_runs
                  ? connected.successful_calls_7d
                    ? "Live calls recorded"
                    : "Live access enabled"
                  : "Replay mode"}
          </Link>
        </footer>
      </div>
    );
  return (
    <div
      className="repair"
      ref={root}
      onKeyDown={(event) => {
        const target = event.target as HTMLElement;
        if (
          target.getAttribute("role") !== "tab" ||
          !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)
        )
          return;
        const tabs = Array.from(
          target.parentElement?.querySelectorAll<HTMLButtonElement>(
            '[role="tab"]',
          ) || [],
        );
        const index = tabs.indexOf(target as HTMLButtonElement);
        const next =
          event.key === "Home"
            ? 0
            : event.key === "End"
              ? tabs.length - 1
              : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) %
                tabs.length;
        event.preventDefault();
        tabs[next]?.focus();
        tabs[next]?.click();
      }}
    >
      <a href="#repair-content" className="r-skip">
        Skip to main content
      </a>
      <aside className="r-sidebar">
        <Link href="/repair" className="r-brand">
          <Mark />
          <span>
            AutoML<span>Autopilot</span>
          </span>
        </Link>
        <p className="r-sidebar-promise">
          The ML coding agent that doesn&apos;t stop at green tests.
        </p>
        {nav}
        <div className="r-sidebar-bottom">
          <div className="r-side-note">
            <Icon name="shield" />
            <strong>You control the merge.</strong>
            <span>
              Every repair comes with evidence. Every release stays yours.
            </span>
          </div>
          <Link href="/autopilot" className="r-nav-link">
            <Icon name="chart" />
            Data incident mode
          </Link>
          <Link href="/repair/settings" className="r-nav-link">
            <Icon name="settings" />
            Workspace settings
          </Link>
          <div className="r-sidebar-footer">
            <span>Autopilot v5</span>
            <Button
              kind="quiet"
              icon={dark ? "sun" : "moon"}
              aria-label={`Switch to ${dark ? "light" : "dark"} theme`}
              onClick={toggle}
            />
          </div>
        </div>
      </aside>
      <div className="r-main">
        <header className="r-topbar">
          <Button
            className="r-mobile-menu"
            kind="quiet"
            icon="menu"
            aria-label="Open navigation"
            onClick={() => setMenu(true)}
          />
          <div className="r-breadcrumb">
            <span>
              {identity.data?.workspaces.find(
                (w) => w.id === identity.data?.current_workspace,
              )?.name || "Workspace"}
            </span>
            <span aria-hidden="true">/</span>
            <strong>{title}</strong>
          </div>
          <Link
            href="/repair/how-it-works#runtime"
            className="r-runtime"
            title={
              status.error || `Runner: ${connected?.runner || "not reported"}`
            }
          >
            <span
              className={`r-status-dot ${connected?.successful_calls_7d ? "r-dot-success" : ""}`}
            />
            {status.loading
              ? "Checking runtime"
              : status.error
                ? "Service unavailable"
                : connected?.live_runs
                  ? connected.successful_calls_7d
                    ? "Live calls recorded"
                    : "Live access enabled"
                  : "Replay mode"}
            <span className="r-runtime-detail">
              {connected
                ? connected.isolated
                  ? "Isolated runner"
                  : "Unisolated runner"
                : "View status"}
            </span>
          </Link>
          <Link
            href="/repair/new"
            className="r-button r-button-primary r-top-new"
            aria-label="New repair"
          >
            <Icon name="plus" size={16} />
            <span>New repair</span>
          </Link>
        </header>
        <main id="repair-content" tabIndex={-1}>
          <div className="r-canvas">{children}</div>
        </main>
        <footer className="r-footer">
          <span>Code correctness. ML integrity. Evidence for both.</span>
          <Link href="/repair/docs?section=privacy">Privacy & retention</Link>
        </footer>
      </div>
      {menu && (
        <Modal title="Navigation" onClose={() => setMenu(false)}>
          {nav}
          <Button onClick={toggle} icon={dark ? "sun" : "moon"}>
            Switch theme
          </Button>
        </Modal>
      )}
    </div>
  );
}
