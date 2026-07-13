import type { RealtimeEvent } from '../types';

type Listener = (event: RealtimeEvent) => void;

/** Tiny pub/sub bridging the WebSocket to interested pages/components. */
class RealtimeBus {
  private listeners = new Set<Listener>();

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  publish(event: RealtimeEvent): void {
    for (const listener of this.listeners) {
      try {
        listener(event);
      } catch (err) {
        console.error('Realtime listener failed', err);
      }
    }
  }
}

export const realtimeBus = new RealtimeBus();
