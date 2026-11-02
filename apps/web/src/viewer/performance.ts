/** Opt-in local benchmark bridge. No scene references are retained after unmount. */
import { useThree, useFrame, type RootState } from "@react-three/fiber";
import { useEffect } from "react";

export const viewerCounters = { parts: 0, viewers: 0 };
declare global {
  interface Window { __r3Viewer?: { state: RootState; counters: typeof viewerCounters } }
}
export function ViewerPerformance() {
  const state = useThree();
  const enabled = localStorage.getItem("r3.viewer.measure") === "1";
  useFrame(() => { if (enabled) state.gl.info.reset(); }, -100);
  useEffect(() => {
    if (localStorage.getItem("r3.viewer.measure") !== "1") return;
    state.gl.info.autoReset = false;
    const host = window as unknown as Record<string, unknown>;
    host.__r3Viewer = { state, counters: viewerCounters };
    return () => { delete host.__r3Viewer; state.gl.info.autoReset = true; };
  }, [state]);
  return null;
}
