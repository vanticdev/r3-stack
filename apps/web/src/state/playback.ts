import { useCallback, useEffect, useRef, useState } from "react";

/** Playback clock for recordings: requestAnimationFrame-driven, ~30 Hz state updates, loops at the end. */
export function usePlayback(start: number, end: number) {
  const [t, setT] = useState(start);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const state = useRef({ t: start, last: 0, emitted: 0 });

  useEffect(() => { state.current.t = start; setT(start); setPlaying(false); }, [start, end]);

  useEffect(() => {
    if (!playing) return;
    let frame = 0;
    state.current.last = performance.now();
    const tick = (now: number) => {
      const s = state.current;
      s.t += ((now - s.last) / 1000) * speed;
      s.last = now;
      if (s.t > end) s.t = start;
      if (now - s.emitted > 33) { s.emitted = now; setT(s.t); }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing, speed, start, end]);

  const seek = useCallback((value: number) => {
    const clamped = Math.min(Math.max(value, start), end);
    state.current.t = clamped;
    setT(clamped);
  }, [start, end]);

  return { t, playing, speed, seek, setSpeed, toggle: () => setPlaying((p) => !p), pause: () => setPlaying(false) };
}

export type Playback = ReturnType<typeof usePlayback>;
