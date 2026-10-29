// The readable project_invalid error: concise message, field paths, "and N more" and the details toggle.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ApiError } from "../api/client.ts";
import type { ApiErrorBody } from "../api/types.ts";
import { ProjectLoadError, projectLoadProblem } from "./ProjectLoadError.tsx";
import { ErrorBox } from "./ui.tsx";

const DUMP = "7 validation errors for RobotSpec\ncomponents.gps.type\n  Field required [type=missing, input_value={...}, input_type=dict]";
const invalid = (over: Partial<ApiErrorBody["data"]> = {}): ApiErrorBody => ({
  code: "project_invalid",
  message: "robot.yaml is invalid: 7 validation errors in RobotSpec; first: components.gps.type: Field required",
  data: {
    project: "rc-car",
    errors: [
      { path: "components.gps.type", message: "Field required", type: "missing" },
      { path: "electrical.harnesses.0.wires", message: "Input should be a valid list", type: "list_type" },
      { path: "requirements.min_top_speed", message: "Input should be a valid number", type: "float_parsing" },
      { path: "software.bindings.gps.pins", message: "Field required", type: "missing" },
      { path: "robot.name", message: "String should have at least 1 character", type: "string_too_short" },
    ],
    total: 7,
    details: DUMP,
    ...over,
  },
});

describe("ProjectLoadError", () => {
  it("shows the concise message, each issue with its field path and how many are not listed", () => {
    const html = renderToStaticMarkup(<ProjectLoadError error={invalid()} />);
    expect(html).toContain("robot.yaml is invalid: 7 validation errors");
    expect(html).toContain("<code>components.gps.type</code><span>Field required</span>");
    expect(html).toContain("<code>electrical.harnesses.0.wires</code>");
    expect(html).toContain("and 2 more");
  });

  it("keeps the full validation text behind a Show details toggle", () => {
    const html = renderToStaticMarkup(<ProjectLoadError error={invalid()} />);
    expect(html).toMatch(/<details class="details"><summary>Show details<\/summary><pre class="code">7 validation errors for RobotSpec/);
    expect(html).not.toMatch(/<details[^>]* open/);
    // The raw dump appears only inside the toggle, never in the headline.
    expect(html.split("<details")[0]).not.toContain("input_value");
  });

  it("omits the count and toggle when there is nothing more to show", () => {
    const html = renderToStaticMarkup(<ProjectLoadError error={invalid({ errors: [{ path: "", message: "line 3, column 5: bad", type: "yaml_syntax" }], total: 1, details: "" })} />);
    expect(html).toContain("line 3, column 5: bad");
    expect(html).not.toContain("<code>");
    expect(html).not.toContain("more");
    expect(html).not.toContain("Show details");
  });

  it("falls back to the plain message for other errors and malformed data", () => {
    expect(projectLoadProblem({ code: "not_found", message: "nope" })).toBeNull();
    expect(renderToStaticMarkup(<ProjectLoadError error={{ code: "internal_error", message: "boom" }} />)).toContain("boom");
    const problem = projectLoadProblem({ code: "project_invalid", message: "bad", data: { errors: "x" } });
    expect(problem).toMatchObject({ message: "bad", errors: [], total: 0, details: "" });
  });

  it("is what ErrorBox renders for a project_invalid API error (Overview and every project page)", () => {
    const body = invalid();
    const html = renderToStaticMarkup(<ErrorBox error={new ApiError(422, body)} />);
    expect(html).toContain('role="alert"');
    expect(html).toContain('data-testid="project-load-error"');
    expect(html).toContain("<code>components.gps.type</code>");
    expect(html).toContain("Show details");
    const other = renderToStaticMarkup(<ErrorBox error={new ApiError(503, { code: "server_code_stale", message: "Restart r3 serve" })} />);
    expect(other).toContain("Restart r3 serve");
    expect(other).not.toContain("project-load-error");
  });
});
