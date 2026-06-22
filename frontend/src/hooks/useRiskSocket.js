// frontend/src/hooks/useRiskSocket.js
//
// ─── WebSocket hook with HTTP polling fallback ────────────────────────────────
//
// Behaviour:
//   1. On mount: open WS connection to ws://localhost:8000/ws/risk
//   2. On message: update riskData state (same shape as useRiskClusters output)
//   3. On error/close: attempt exponential-backoff reconnect
//      • Attempts: 1s → 2s → 4s → 8s → 16s → 30s (capped)
//      • After 3 failed attempts: fall back to HTTP polling (useRiskClusters)
//      • On reconnect success: stop polling, switch back to WS live mode
//   4. On unmount: close WS cleanly, clear all timers
//
// Status values emitted:
//   'connecting'    — WS handshake in progress
//   'live'          — WS connected and receiving messages
//   'reconnecting'  — WS dropped, retrying with backoff
//   'polling'       — fallback HTTP polling active (WS unreachable after 3 retries)
//   'error'         — persistent failure (shown in UI badge)

import { useState, useEffect, useRef, useCallback } from 'react';
import { useRiskClusters } from './useRiskClusters';

// Build the WebSocket URL dynamically so it works through Vite's /ws proxy
// in dev (ws://localhost:5173/ws/risk → proxied to ws://localhost:8000/ws/risk)
// and directly in production builds.
const WS_URL = (() => {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  // Use VITE_API_BASE_URL if available (for production cross-domain)
  const baseUrl = import.meta.env.VITE_API_BASE_URL;
  if (baseUrl) {
    return baseUrl.replace(/^http/, 'ws') + '/ws/risk';
  }
  return `${protocol}//${window.location.host}/ws/risk`;
})();
const MAX_RECONNECT_ATTEMPTS = 3;
const BACKOFF_BASE_MS = 1000;  // 1s → 2s → 4s → 8s → 16s (capped at 30s)
const MAX_BACKOFF_MS = 30000;

export const useRiskSocket = () => {
  // ── State ─────────────────────────────────────────────────────────────────
  const [riskData, setRiskData] = useState({
    generated_at: null,
    cluster_count: 0,
    clusters: [],
    loading: true,
    error: null,
  });
  const [wsStatus, setWsStatus] = useState('connecting');

  // ── Internal refs (don't trigger re-renders) ──────────────────────────────
  const wsRef = useRef(null);
  const reconnectAttempts = useRef(0);
  const reconnectTimer = useRef(null);
  const isMounted = useRef(true);
  const usingFallback = useRef(false);

  // ── Fallback polling hook ─────────────────────────────────────────────────
  // useRiskClusters is always rendered (hooks must be called unconditionally),
  // but its output is only consumed when usingFallback is true.
  // Setting pollIntervalMs to a very large value (24h) effectively disables it.
  const [fallbackPollInterval, setFallbackPollInterval] = useState(86400000); // ~disabled
  const polledData = useRiskClusters(0.0, 100, fallbackPollInterval);

  // When fallback is active, mirror polled data into riskData
  useEffect(() => {
    if (usingFallback.current) {
      setRiskData({
        generated_at: polledData.generated_at,
        cluster_count: polledData.cluster_count,
        clusters: polledData.clusters,
        loading: polledData.loading,
        error: polledData.error,
      });
    }
  }, [
    polledData.generated_at,
    polledData.cluster_count,
    polledData.clusters,
    polledData.loading,
    polledData.error,
  ]);

  // ── Activate HTTP polling fallback ────────────────────────────────────────
  const activateFallback = useCallback(() => {
    if (!isMounted.current) return;
    console.log('[WS] Switching to HTTP polling fallback (5s interval)');
    usingFallback.current = true;
    setWsStatus('polling');
    // Activate the polling hook by setting a real interval
    setFallbackPollInterval(5000);
  }, []);

  // ── Deactivate HTTP polling (WS reconnected) ──────────────────────────────
  const deactivateFallback = useCallback(() => {
    if (!isMounted.current) return;
    console.log('[WS] WebSocket reconnected — stopping HTTP polling fallback');
    usingFallback.current = false;
    setFallbackPollInterval(86400000); // effectively disable polling
  }, []);

  // ── Connect / Reconnect ───────────────────────────────────────────────────
  const connect = useCallback(() => {
    if (!isMounted.current) return;

    // Clean up any existing connection before opening a new one
    if (wsRef.current) {
      wsRef.current.onopen = null;
      wsRef.current.onmessage = null;
      wsRef.current.onerror = null;
      wsRef.current.onclose = null;
      wsRef.current.close();
      wsRef.current = null;
    }

    setWsStatus('connecting');
    console.log('[WS] Connecting to', WS_URL);

    let ws;
    try {
      ws = new WebSocket(WS_URL);
    } catch (err) {
      console.error('[WS] Failed to create WebSocket:', err);
      scheduleReconnect();
      return;
    }

    wsRef.current = ws;

    ws.onopen = () => {
      if (!isMounted.current) return;
      console.log('[WS] Connected');
      reconnectAttempts.current = 0;
      setWsStatus('live');
      // If we were previously polling, switch back to WS
      if (usingFallback.current) {
        deactivateFallback();
      }
    };

    ws.onmessage = (event) => {
      if (!isMounted.current) return;
      try {
        const payload = JSON.parse(event.data);
        setRiskData({
          generated_at: payload.generated_at,
          cluster_count: payload.cluster_count,
          clusters: payload.clusters || [],
          loading: false,
          error: null,
        });
      } catch (err) {
        console.error('[WS] Failed to parse message:', err);
      }
    };

    ws.onerror = (err) => {
      // onerror always fires before onclose — we handle retry in onclose
      console.warn('[WS] Connection error', err);
    };

    ws.onclose = (event) => {
      if (!isMounted.current) return;
      console.log('[WS] Connection closed. Code:', event.code, 'Reason:', event.reason);
      wsRef.current = null;
      scheduleReconnect();
    };
  }, [deactivateFallback]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Exponential backoff reconnect ─────────────────────────────────────────
  const scheduleReconnect = useCallback(() => {
    if (!isMounted.current) return;

    reconnectAttempts.current += 1;

    if (reconnectAttempts.current > MAX_RECONNECT_ATTEMPTS) {
      console.warn(
        `[WS] ${reconnectAttempts.current} reconnect attempts failed — switching to HTTP polling fallback`
      );
      activateFallback();
      return; // Stop attempting WS reconnects once fallback is active
    }

    const backoffMs = Math.min(
      BACKOFF_BASE_MS * Math.pow(2, reconnectAttempts.current - 1),
      MAX_BACKOFF_MS
    );

    console.log(
      `[WS] Reconnect attempt ${reconnectAttempts.current}/${MAX_RECONNECT_ATTEMPTS} in ${backoffMs}ms`
    );
    setWsStatus('reconnecting');

    reconnectTimer.current = setTimeout(() => {
      if (isMounted.current) {
        connect();
      }
    }, backoffMs);
  }, [activateFallback, connect]);

  // ── Mount / Unmount ───────────────────────────────────────────────────────
  useEffect(() => {
    isMounted.current = true;
    connect();

    return () => {
      isMounted.current = false;
      // Clear pending reconnect timer
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current);
        reconnectTimer.current = null;
      }
      // Close WS cleanly
      if (wsRef.current) {
        wsRef.current.onopen = null;
        wsRef.current.onmessage = null;
        wsRef.current.onerror = null;
        wsRef.current.onclose = null;
        wsRef.current.close(1000, 'Component unmounted');
        wsRef.current = null;
      }
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return { riskData, wsStatus };
};
