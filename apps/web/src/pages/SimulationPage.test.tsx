import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { JointFailures, jointFailures } from "./SimulationPage.tsx";

const failure = { event: "joint_failed", t: 0.5, joint: "S3", load: 40, capacity: 0,
  message: "S3: needs 40 N, holds 0 N: no nut" };

describe("joint failure playback", () => {
  it("keeps only recorded joint failures", () => {
    expect(jointFailures([null, {}, { event: "brownout", t: 1 }, failure])).toEqual([failure]);
  });
  it("shows the shortfall and a seek control at the recorded release time", () => {
    const html = renderToStaticMarkup(<JointFailures events={[failure]} t={1} seek={() => {}} />);
    expect(html).toContain("needs 40 N, holds 0 N: no nut");
    expect(html).toContain("t = 0.500 s");
    expect(html).toContain("Released");
    expect(html).toContain("Release blocked");
  });
  it("marks future events without hiding their diagnosis", () => {
    expect(renderToStaticMarkup(<JointFailures events={[failure]} t={0} seek={() => {}} />)).toContain("Upcoming");
  });
});
