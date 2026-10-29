import type { Playback } from "../state/playback.ts";
import { Icon } from "./icons.tsx";

export function Timeline({ playback, start, end, label }: { playback: Playback; start: number; end: number; label?: string }) {
  return (
    <div className="timeline" data-testid="timeline">
      <button className="small" onClick={playback.toggle} aria-label={playback.playing ? "Pause" : "Play"}>
        <Icon name={playback.playing ? "pause" : "play"} size={13} />{playback.playing ? "Pause" : "Play"}
      </button>
      <select value={playback.speed} onChange={(e) => playback.setSpeed(Number(e.target.value))} aria-label="Playback speed">
        {[0.1, 0.25, 0.5, 1, 2, 4].map((s) => <option key={s} value={s}>{s}×</option>)}
      </select>
      <input type="range" min={start} max={end} step="any" value={playback.t} aria-label="Scrub"
             onChange={(e) => { playback.pause(); playback.seek(Number(e.target.value)); }} />
      <span className="time">{playback.t.toFixed(2)} / {end.toFixed(2)} s</span>
      {label && <span className="small muted">{label}</span>}
    </div>
  );
}
