import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { Mesh } from "three";

import type { PlaybackRecording, Vec3 } from "../api/types.ts";
import { PlaybackCables, playbackTubeGeometry } from "./RobotViewer.tsx";

const points: Vec3[] = [[0, 0, 0], [0.02, 0, 0.002], [0.04, 0, 0]];

describe("conductor playback", () => {
  it("builds one tube per conductor with its recorded radius and colour", () => {
    const cables = {
      W_PWR: { wire_id: "W_PWR", bundle: "cable00", od: 0.004, color: "red", rgba: null, points: [points] },
      W_GND: { wire_id: "W_GND", bundle: "cable00", od: 0.0012, color: "black", rgba: null, points: [points] },
    };
    const recording = { frames: { cables } } as unknown as PlaybackRecording;
    const warnings = vi.spyOn(console, "error").mockImplementation(() => {});
    const markup = renderToStaticMarkup(<PlaybackCables recording={recording} meshes={{ current: new Map<string, Mesh>() }} />);
    warnings.mockRestore();
    expect(markup.match(/<mesh>/g)).toHaveLength(2);
    for (const cable of Object.values(cables)) {
      const geometry = playbackTubeGeometry(cable, points);
      expect(geometry.parameters.radius).toBe(cable.od / 2);
      expect(geometry.getAttribute("position").count).toBeGreaterThan(points.length);
      geometry.dispose();
    }
    expect(markup).toContain('color="14034728"');
    expect(markup).toContain('color="2763306"');
  });
});
