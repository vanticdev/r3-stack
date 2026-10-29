import type { ApiErrorBody, ProjectInvalidData, ProjectLoadIssue } from "../api/types.ts";

/** Issues listed before "and N more"; the API sends at most five. */
export const SHOWN_ISSUES = 5;

/** The readable part of a `project_invalid` error body, or null for any other error. */
export function projectLoadProblem(body: ApiErrorBody | null | undefined): (ProjectInvalidData & { message: string }) | null {
  if (!body || body.code !== "project_invalid") return null;
  const data = (body.data ?? {}) as Partial<ProjectInvalidData>;
  const errors = Array.isArray(data.errors)
    ? data.errors.filter((e): e is ProjectLoadIssue => !!e && typeof e === "object" && typeof e.message === "string")
    : [];
  return {
    ...data,
    message: body.message,
    errors,
    total: typeof data.total === "number" ? data.total : errors.length,
    details: typeof data.details === "string" ? data.details : "",
  };
}

/** A project whose robot.yaml (or catalog) does not load: the concise message, the first issues with
 * their field paths, and the full validation text behind "Show details". */
export function ProjectLoadError({ error, compact = false }: { error: ApiErrorBody; compact?: boolean }) {
  const problem = projectLoadProblem(error);
  if (!problem) return <span className="load-error-message">{error.message}</span>;
  const shown = problem.errors.slice(0, SHOWN_ISSUES);
  const more = problem.total - shown.length;
  return (
    <div className={`load-error${compact ? " compact" : ""}`} data-testid="project-load-error">
      <strong className="load-error-message">{problem.message}</strong>
      {shown.length > 0 && (
        <ul className="load-error-issues">
          {shown.map((issue, i) => (
            <li key={`${issue.path}-${i}`}>
              {issue.path && <code>{issue.path}</code>}
              <span>{issue.message}</span>
            </li>
          ))}
          {more > 0 && <li className="muted">and {more} more</li>}
        </ul>
      )}
      {problem.details && (
        <details className="details">
          <summary>Show details</summary>
          <pre className="code">{problem.details}</pre>
        </details>
      )}
    </div>
  );
}
