import type { Metadata } from "next";
import "./repair.css";
export const metadata: Metadata = {
  title: {
    default: "AutoML Autopilot — evidence-backed ML repairs",
    template: "%s | AutoML Autopilot",
  },
  description:
    "The ML coding agent that doesn't stop at green tests. Investigate, repair and independently verify your ML repository.",
};
export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
