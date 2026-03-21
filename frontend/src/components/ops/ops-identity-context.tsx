"use client";

import { createContext, useContext, useEffect, useMemo, useState } from "react";
import type { LabRole } from "@/lib/ops-types";

interface OpsIdentityContextValue {
  actorName: string;
  actorRole: LabRole;
  setActorName: (value: string) => void;
  setActorRole: (value: LabRole) => void;
}

const DEFAULT_NAME = "manager";
const DEFAULT_ROLE: LabRole = "admin";
const STORAGE_NAME_KEY = "ops_actor_name";
const STORAGE_ROLE_KEY = "ops_actor_role";

const OpsIdentityContext = createContext<OpsIdentityContextValue | null>(null);

export function OpsIdentityProvider({ children }: { children: React.ReactNode }) {
  const [actorName, setActorName] = useState(() => {
    if (typeof window === "undefined") {
      return DEFAULT_NAME;
    }
    return window.localStorage.getItem(STORAGE_NAME_KEY)?.trim() || DEFAULT_NAME;
  });
  const [actorRole, setActorRole] = useState<LabRole>(() => {
    if (typeof window === "undefined") {
      return DEFAULT_ROLE;
    }
    const storedRole = window.localStorage.getItem(STORAGE_ROLE_KEY) as LabRole | null;
    return storedRole && ["viewer", "operator", "approver", "admin"].includes(storedRole)
      ? storedRole
      : DEFAULT_ROLE;
  });

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    window.localStorage.setItem(STORAGE_NAME_KEY, actorName);
  }, [actorName]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    window.localStorage.setItem(STORAGE_ROLE_KEY, actorRole);
  }, [actorRole]);

  const value = useMemo(() => ({
    actorName,
    actorRole,
    setActorName,
    setActorRole,
  }), [actorName, actorRole]);

  return <OpsIdentityContext.Provider value={value}>{children}</OpsIdentityContext.Provider>;
}

export function useOpsIdentity() {
  const context = useContext(OpsIdentityContext);
  if (!context) {
    throw new Error("useOpsIdentity must be used inside OpsIdentityProvider");
  }
  return context;
}
