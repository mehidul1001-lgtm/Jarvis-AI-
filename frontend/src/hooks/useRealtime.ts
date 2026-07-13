import { useEffect, useRef, useState } from 'react';
import { websocketUrl } from '../api/client';
import type { RealtimeEvent } from '../types';
import { realtimeBus } from './realtimeBus';

export type RealtimeStatus = 'connecting' | 'connected' | 'disconnected';

/**
 * Maintains the authenticated realtime WebSocket channel with
 * exponential-backoff reconnection. Phase 2 will layer chat streaming and
 * notifications on top of this connection.
 */
export function useRealtime(enabled: boolean): RealtimeStatus {
  const [status, setStatus] = useState<RealtimeStatus>('disconnected');
  const attemptRef = useRef(0);

  useEffect(() => {
    if (!enabled) {
      setStatus('disconnected');
      return;
    }

    let socket: WebSocket | null = null;
    let reconnectTimer: number | undefined;
    let pingTimer: number | undefined;
    let disposed = false;

    const connect = () => {
      if (disposed) return;
      setStatus('connecting');
      socket = new WebSocket(websocketUrl());

      socket.onopen = () => {
        attemptRef.current = 0;
        setStatus('connected');
        pingTimer = window.setInterval(() => {
          if (socket?.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ type: 'ping' }));
          }
        }, 30_000);
      };

      socket.onmessage = (message: MessageEvent<string>) => {
        try {
          const event = JSON.parse(message.data) as RealtimeEvent;
          if (event.type && event.type !== 'pong') realtimeBus.publish(event);
        } catch {
          // Ignore malformed frames.
        }
      };

      socket.onclose = () => {
        window.clearInterval(pingTimer);
        setStatus('disconnected');
        if (disposed) return;
        const delay = Math.min(30_000, 1000 * 2 ** attemptRef.current);
        attemptRef.current += 1;
        reconnectTimer = window.setTimeout(connect, delay);
      };

      socket.onerror = () => {
        socket?.close();
      };
    };

    connect();
    return () => {
      disposed = true;
      window.clearTimeout(reconnectTimer);
      window.clearInterval(pingTimer);
      socket?.close();
    };
  }, [enabled]);

  return status;
}
