import { expect, it } from "vitest";
import { pcbRender } from "./pcb.ts";
import { pcbPinMap } from "./domain.ts";

it("never displays a copper layer as the 3D render", () => {
  const files = ["copper_top.svg", "render_top.png"].map((name) => ({ name, path: `pcb/board/${name}`, url: `/${name}` }));
  expect(pcbRender(files, "3d", "top")?.url).toBe("/render_top.png");
  expect(pcbRender(files, "copper", "top")?.url).toBe("/copper_top.svg");
  expect(pcbRender(files.slice(0, 1), "3d", "top")).toBeUndefined();
});

it("reports unexpected and wrongly named connector pins as mismatches", () => {
  const pins = pcbPinMap({ J1: { "1": "GND", "2": "unexpected", "3": "board.VCC" } }, { J1: { "1": "GND", "3": "VCC" } });
  expect(pins.map((p) => p.match)).toEqual([true, false, false]);
});
