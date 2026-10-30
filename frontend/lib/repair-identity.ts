"use client";
import { useEffect, useState } from "react";
import { useResource } from "./repair";

export type Role = "viewer" | "member" | "admin" | "owner";
export interface Workspace {
  id: string;
  name: string;
  role: Role;
  tenant?: string;
}
export interface Identity {
  account: { id: string; login: string; name: string; avatar_url?: string };
  personal_workspace: string;
  current_workspace: string;
  workspaces: Workspace[];
  role: Role;
}
export function useIdentity() {
  const [signedIn, setSignedIn] = useState(false);
  const [initializing, setInitializing] = useState(true);
  const [authError, setAuthError] = useState<string | null>(null);
  useEffect(() => {
    const update = () => {
      setSignedIn(!!sessionStorage.getItem("repair-token"));
      setAuthError(sessionStorage.getItem("repair-auth-error"));
      setInitializing(false);
    };
    update();
    window.addEventListener("repair-identity", update);
    return () => window.removeEventListener("repair-identity", update);
  }, []);
  const resource = useResource<Identity>(signedIn ? "/me" : null);
  const role = signedIn ? resource.data?.role : "owner";
  return {
    ...resource,
    error: authError || resource.error,
    signedIn,
    ready: !initializing && (!signedIn || !resource.loading),
    role,
    canWrite:
      !initializing && (!signedIn || (!!resource.data && role !== "viewer")),
    canAdmin: signedIn && (role === "admin" || role === "owner"),
  };
}
export function selectWorkspace(id: string) {
  localStorage.setItem("repair-workspace", id);
  // Discard all mounted task/repository state when crossing a tenant boundary.
  window.location.assign("/repair/runs");
}
export function signOut() {
  sessionStorage.removeItem("repair-token");
  sessionStorage.removeItem("repair-auth-error");
  localStorage.removeItem("repair-workspace");
  window.location.assign("/repair/settings");
}
