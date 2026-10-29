"use client";
import {
  cloneElement,
  isValidElement,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import Link from "next/link";
import { label, copy } from "@/lib/repair";

const paths: Record<string, React.ReactNode> = {
  home: (
    <>
      <path d="m3 10 9-7 9 7v10H3Z" />
      <path d="M9 20v-7h6v7" />
    </>
  ),
  plus: <path d="M12 5v14M5 12h14" />,
  play: <path d="m9 5 11 7-11 7Z" />,
  pause: <path d="M8 5v14M16 5v14" />,
  code: <path d="m8 6-6 6 6 6m8-12 6 6-6 6m-3-14-2 16" />,
  check: <path d="m5 12 4 4L19 6" />,
  close: <path d="m6 6 12 12M6 18 18 6" />,
  arrow: <path d="M4 12h15m-6-6 6 6-6 6" />,
  shield: (
    <>
      <path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z" />
      <path d="m8 12 3 3 5-6" />
    </>
  ),
  branch: (
    <>
      <circle cx="6" cy="5" r="2" />
      <circle cx="6" cy="19" r="2" />
      <circle cx="18" cy="5" r="2" />
      <path d="M6 7v10m0-5h6a6 6 0 0 0 6-5" />
    </>
  ),
  book: (
    <>
      <path d="M12 6v15M3 4c4-1 7 0 9 2 2-2 5-3 9-2v15c-4-1-7 0-9 2-2-2-5-3-9-2Z" />
    </>
  ),
  chart: (
    <>
      <path d="M4 3v17h17M9 16V9m5 7V5m5 11v-5" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  menu: <path d="M4 6h16M4 12h16M4 18h16" />,
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 1v2m0 18v2M1 12h2m18 0h2M4 4l2 2m12 12 2 2M4 20l2-2M18 6l2-2" />
    </>
  ),
  moon: <path d="M20 15A9 9 0 0 1 9 4a9 9 0 1 0 11 11Z" />,
  download: <path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5" />,
  copy: (
    <>
      <rect x="8" y="8" width="12" height="13" rx="2" />
      <path d="M16 8V3H3v13h5" />
    </>
  ),
  alert: (
    <>
      <path d="m12 3 10 18H2Z" />
      <path d="M12 9v5m0 3v1" />
    </>
  ),
  settings: (
    <>
      <path d="M4 7h16M4 17h16" />
      <circle cx="9" cy="7" r="3" />
      <circle cx="16" cy="17" r="3" />
    </>
  ),
  external: (
    <>
      <path d="M14 3h7v7m0-7L10 14M10 3H3v18h18v-7" />
    </>
  ),
  upload: <path d="M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5" />,
  search: (
    <>
      <circle cx="10" cy="10" r="6" />
      <path d="m15 15 6 6" />
    </>
  ),
  terminal: (
    <>
      <rect x="2" y="4" width="20" height="16" rx="2" />
      <path d="m6 8 4 4-4 4m7 0h5" />
    </>
  ),
};
export function Icon({ name, size = 20 }: { name: string; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name] || paths.code}
    </svg>
  );
}
export function Mark() {
  return (
    <svg
      width="34"
      height="34"
      viewBox="0 0 34 34"
      fill="none"
      aria-hidden="true"
    >
      <rect width="34" height="34" rx="10" fill="currentColor" />
      <path
        d="m9 23 7-13 9 13m-13-5h10"
        stroke="var(--r-on-accent)"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
export function Badge({
  value,
  children,
}: {
  value?: string | null;
  children?: React.ReactNode;
}) {
  const tone = ["VERIFIED", "PASS", "APPROVED"].includes(value || "")
    ? "success"
    : ["REJECTED", "FAIL", "FAILED", "SYSTEM_FAILURE"].includes(value || "")
      ? "danger"
      : ["INCONCLUSIVE", "BLOCKED", "budget", "warning"].includes(value || "")
        ? "warning"
        : "neutral";
  return (
    <span className={`r-badge r-${tone}`}>
      {tone === "success" ? (
        <Icon name="check" size={13} />
      ) : tone === "danger" ? (
        <Icon name="close" size={13} />
      ) : null}
      {children || label(value)}
    </span>
  );
}
export function Button({
  children,
  icon,
  kind = "secondary",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  icon?: string;
  kind?: "primary" | "secondary" | "quiet" | "danger";
}) {
  return (
    <button
      {...props}
      className={`r-button r-button-${kind} ${props.className || ""}`}
    >
      {icon && <Icon name={icon} size={17} />} {children}
    </button>
  );
}
export function Action({
  href,
  children,
  primary = false,
  icon,
}: {
  href: string;
  children: React.ReactNode;
  primary?: boolean;
  icon?: string;
}) {
  return (
    <Link
      className={`r-button r-button-${primary ? "primary" : "secondary"}`}
      href={href}
    >
      {icon && <Icon name={icon} size={17} />} {children}
    </Link>
  );
}
export function Notice({
  title,
  children,
  tone = "info",
  action,
}: {
  title: string;
  children?: React.ReactNode;
  tone?: string;
  action?: React.ReactNode;
}) {
  return (
    <div
      className={`r-notice r-notice-${tone}`}
      role={tone === "error" ? "alert" : "status"}
    >
      <Icon
        name={tone === "error" || tone === "warning" ? "alert" : "shield"}
      />
      <div>
        <strong>{title}</strong>
        {children && <div>{children}</div>}
        {action && <div className="r-actions">{action}</div>}
      </div>
    </div>
  );
}
export function PageHead({
  title,
  intro,
  action,
}: {
  title: string;
  intro?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="r-page-head">
      <div>
        <h1>{title}</h1>
        {intro && <p>{intro}</p>}
      </div>
      {action && <div className="r-actions">{action}</div>}
    </div>
  );
}
export function Panel({
  title,
  subtitle,
  children,
  action,
  className = "",
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`r-panel ${className}`}>
      <div className="r-panel-head">
        <div>
          <h2>{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
        {action}
      </div>
      <div className="r-panel-body">{children}</div>
    </section>
  );
}
export function Empty({
  title,
  children,
  action,
}: {
  title: string;
  children?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="r-empty">
      <Icon name="branch" size={30} />
      <h2>{title}</h2>
      <p>{children}</p>
      {action}
    </div>
  );
}
export function Copy({
  value,
  title = "Copy",
}: {
  value: string;
  title?: string;
}) {
  const [done, setDone] = useState(false),
    [failed, setFailed] = useState(false);
  return (
    <>
      <Button
        icon={done ? "check" : "copy"}
        onClick={async () => {
          try {
            await copy(value);
            setDone(true);
            setFailed(false);
            setTimeout(() => setDone(false), 2000);
          } catch {
            setFailed(true);
          }
        }}
      >
        {done ? "Copied" : title}
      </Button>
      {failed && (
        <span role="status">Select the text to copy it manually.</span>
      )}
    </>
  );
}
export function Modal({
  title,
  children,
  onClose,
}: {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    const node = ref.current;
    node?.showModal();
    return () => {
      node?.close();
      previous?.focus();
    };
  }, []);
  return (
    <dialog
      className="r-dialog"
      ref={ref}
      aria-labelledby="repair-dialog-title"
      onCancel={onClose}
    >
      <div className="r-panel-head">
        <h2 id="repair-dialog-title">{title}</h2>
        <Button
          aria-label="Close dialog"
          icon="close"
          kind="quiet"
          onClick={onClose}
        />
      </div>
      <div className="r-panel-body">{children}</div>
    </dialog>
  );
}
export function Field({
  label: text,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: React.ReactNode;
}) {
  const id = useId();
  const control = isValidElement(children)
    ? cloneElement(children as React.ReactElement<Record<string, unknown>>, {
        "aria-labelledby": id,
        "aria-describedby": hint ? `${id}-hint` : undefined,
        "aria-invalid": error ? true : undefined,
        "aria-errormessage": error ? `${id}-error` : undefined,
      })
    : children;
  return (
    <label className="r-field">
      <span id={id}>{text}</span>
      {control}
      {hint && <small id={`${id}-hint`}>{hint}</small>}
      {error && (
        <small id={`${id}-error`} className="r-error" role="alert">
          {error}
        </small>
      )}
    </label>
  );
}
export function Loading({
  text = "Loading repair evidence…",
}: {
  text?: string;
}) {
  return (
    <div className="r-loading" role="status">
      <span className="r-spinner" />
      {text}
    </div>
  );
}
