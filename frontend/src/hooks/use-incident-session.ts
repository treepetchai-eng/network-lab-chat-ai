"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createIncidentSession, deleteSession } from "@/lib/api";

export function useIncidentSession(incidentNo: string) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const sessionRef = useRef<string | null>(null);
  const mountedRef = useRef(false);
  const generationRef = useRef(0);

  const createFreshSession = useCallback(async (generation: number, targetIncidentNo: string) => {
    const id = await createIncidentSession(targetIncidentNo);

    if (!mountedRef.current || generationRef.current !== generation) {
      void deleteSession(id, { keepalive: true }).catch(() => {});
      return null;
    }

    sessionRef.current = id;
    setSessionId(id);
    setError(null);
    return id;
  }, []);

  const loadSession = useCallback(async (targetIncidentNo: string) => {
    const generation = generationRef.current + 1;
    const previousId = sessionRef.current;

    generationRef.current = generation;
    sessionRef.current = null;
    setSessionId(null);
    setError(null);
    setIsLoading(true);

    if (previousId) {
      await deleteSession(previousId).catch(() => {});
    }

    try {
      return await createFreshSession(generation, targetIncidentNo);
    } catch (err) {
      if (mountedRef.current && generationRef.current === generation) {
        setSessionId(null);
        setError(err instanceof Error ? err.message : "Failed to create incident session");
      }
      return null;
    } finally {
      if (mountedRef.current && generationRef.current === generation) {
        setIsLoading(false);
      }
    }
  }, [createFreshSession]);

  const retrySession = useCallback(async () => loadSession(incidentNo), [incidentNo, loadSession]);

  useEffect(() => {
    mountedRef.current = true;

    return () => {
      mountedRef.current = false;
      generationRef.current += 1;

      const activeSession = sessionRef.current;
      sessionRef.current = null;

      if (activeSession) {
        void deleteSession(activeSession, { keepalive: true }).catch(() => {});
      }
    };
  }, []);

  useEffect(() => {
    if (!mountedRef.current) {
      return;
    }
    void loadSession(incidentNo);
  }, [incidentNo, loadSession]);

  useEffect(() => {
    const handlePageHide = () => {
      const activeSession = sessionRef.current;
      if (!activeSession) {
        return;
      }

      generationRef.current += 1;
      sessionRef.current = null;
      void deleteSession(activeSession, { keepalive: true }).catch(() => {});
    };

    window.addEventListener("pagehide", handlePageHide);
    return () => {
      window.removeEventListener("pagehide", handlePageHide);
    };
  }, []);

  return { sessionId, isLoading, error, retrySession };
}
