"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { createSession, deleteSession } from "@/lib/api";

export function useSession() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const sessionRef = useRef<string | null>(null);
  const mountedRef = useRef(false);
  const generationRef = useRef(0);

  const createFreshSession = useCallback(async (generation: number) => {
    const id = await createSession();

    if (!mountedRef.current || generationRef.current !== generation) {
      void deleteSession(id, { keepalive: true }).catch(() => {});
      return null;
    }

    sessionRef.current = id;
    setSessionId(id);
    return id;
  }, []);

  const initSession = useCallback(async () => {
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    setIsLoading(true);

    try {
      return await createFreshSession(generation);
    } catch {
      if (mountedRef.current && generationRef.current === generation) {
        sessionRef.current = null;
        setSessionId(null);
      }
      return null;
    } finally {
      if (mountedRef.current && generationRef.current === generation) {
        setIsLoading(false);
      }
    }
  }, [createFreshSession]);

  const resetSession = useCallback(async () => {
    const generation = generationRef.current + 1;
    const previousId = sessionRef.current;

    generationRef.current = generation;
    sessionRef.current = null;
    setSessionId(null);
    setIsLoading(true);

    if (previousId) {
      await deleteSession(previousId).catch(() => {});
    }

    try {
      return await createFreshSession(generation);
    } catch {
      if (mountedRef.current && generationRef.current === generation) {
        setSessionId(null);
      }
      return null;
    } finally {
      if (mountedRef.current && generationRef.current === generation) {
        setIsLoading(false);
      }
    }
  }, [createFreshSession]);

  useEffect(() => {
    mountedRef.current = true;
    void initSession();

    return () => {
      mountedRef.current = false;
      generationRef.current += 1;

      const activeSession = sessionRef.current;
      sessionRef.current = null;

      if (activeSession) {
        void deleteSession(activeSession, { keepalive: true }).catch(() => {});
      }
    };
  }, [initSession]);

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

  return { sessionId, isLoading, resetSession };
}
