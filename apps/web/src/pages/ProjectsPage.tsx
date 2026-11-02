import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api/client.ts";
import { keys, useProjects } from "../api/hooks.ts";
import type { ProjectSummary } from "../api/types.ts";
import { Markdown } from "../components/Markdown.tsx";
import { Icon } from "../components/icons.tsx";
import { ProjectLoadError } from "../components/ProjectLoadError.tsx";
import { Card, Details, Dialog, Empty, ErrorBox, QueryView } from "../components/ui.tsx";
import { stageLabel } from "../lib/labels.ts";
import { countsText, listLabels } from "../lib/status.ts";
import { timeAgo } from "../lib/units.ts";

const TEMPLATES: Record<string, { label: string; description: string }> = {
  blank: { label: "Empty project", description: "A robot.yaml with the schema header and no components." },
};

function ProjectStatusLine({ p }: { p: ProjectSummary }) {
  const run = p.last_run;
  const failed = Object.entries(p.stages).filter(([, s]) => s === "failed").map(([stage]) => stageLabel(stage));
  const to = `/p/${encodeURIComponent(p.id)}`;
  if (p.error_detail) return <div className="status"><ProjectLoadError error={p.error_detail} compact /></div>;
  if (p.error) return <div className="status"><span style={{ color: "var(--critical)" }}>{p.error}</span></div>;
  if (!run) return <div className="status"><span className="muted">Not run yet</span></div>;
  return (
    <div className="status">
      {run.stale ? <strong>Results out of date</strong> : run.status === "ok" ? <strong>All checks pass</strong> : <strong>Checks failing</strong>}
      <span className="secondary">{run.stale ? "Previous run" : "Last run"} {timeAgo(run.finished_at)}: {countsText(run.counts ?? {})}</span>
      {failed.length > 0 && <span className="secondary">Failed: {listLabels(failed, 4)}</span>}
      {!!run.counts?.error && <Link to={`${to}/diagnostics`}>View errors</Link>}
    </div>
  );
}

export function ProjectsPage() {
  const projects = useProjects();
  const [creating, setCreating] = useState(false);
  return (
    <div className="content">
      <div className="page" style={{ maxWidth: 1080 }}>
        <div className="page-head">
          <h1>Projects</h1>
          <button className="primary" onClick={() => setCreating(true)}><Icon name="plus" size={14} />New project</button>
        </div>
        <QueryView query={projects} height={160}>
          {({ projects: list }) => (
            <Card flush>
              {list.length ? (
                <div className="list">
                  {list.map((p) => (
                    <div key={p.id} className="project-row" data-testid="project-card">
                      <div className="stack" style={{ gap: 2, minWidth: 0 }}>
                        <Link to={`/p/${encodeURIComponent(p.id)}/overview`} className="name">{p.name}</Link>
                        <span className="muted small">{timeAgo(p.last_run?.finished_at)}</span>
                      </div>
                      <span className="actions">{p.error || p.error_detail ? "Invalid" : p.last_run?.stale ? "Outdated" : !p.last_run ? "Unchecked" : p.last_run.status === "ok" ? "Passed" : "Blocked"}</span>
                      <Details summary="Details">
                      <span>{p.id}{p.version ? ` · v${p.version}` : ""}</span>
                      {p.description && <span className="desc" title={p.description}><Markdown inline>{p.description.split("\n")[0] ?? ""}</Markdown></span>}
                      <ProjectStatusLine p={p} /></Details>
                    </div>
                  ))}
                </div>
              ) : <Empty title="No projects yet"><button className="primary" onClick={() => setCreating(true)}>New project</button></Empty>}
            </Card>
          )}
        </QueryView>
      </div>
      {creating && <CreateProjectDialog templates={projects.data?.templates ?? []} onClose={() => setCreating(false)} />}
    </div>
  );
}

function CreateProjectDialog({ templates, onClose }: { templates: string[]; onClose: () => void }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [template, setTemplate] = useState("blank");
  const [github, setGithub] = useState(false);
  const [isPrivate, setPrivate] = useState(true);
  const [confirming, setConfirming] = useState(false);
  const create = useMutation({
    mutationFn: () => api.createProject({ name, template, ...(github ? { github: { private: isPrivate }, confirm: true } : {}) }),
    onSuccess: async (result) => {
      await client.invalidateQueries({ queryKey: keys.projects });
      navigate(`/p/${encodeURIComponent(result.id)}/overview`);
    },
  });
  const options = [...new Set(["blank", ...templates])];
  const valid = /^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(name);
  const submit = () => { if (github && !confirming) setConfirming(true); else create.mutate(); };
  return (
    <Dialog title={confirming ? "Create the GitHub repository?" : "New project"} onClose={onClose} wide
            footer={<>
              <button onClick={confirming ? () => setConfirming(false) : onClose}>{confirming ? "Back" : "Cancel"}</button>
              <button className="primary" type="submit" form="create-project" disabled={!valid || create.isPending}>
                {create.isPending ? "Creating…" : confirming ? "Create project and repository" : "Create project"}
              </button>
            </>}>
      <form id="create-project" className="stack" style={{ gap: 14 }} onSubmit={(e) => { e.preventDefault(); if (valid) submit(); }}>
        {confirming ? (
          <p>This creates <strong>{isPrivate ? "a private" : "a public"}</strong> repository <code>{name}</code> under the GitHub account signed in to the
            <code> gh</code> CLI on the server, and pushes the new project to it.</p>
        ) : (
          <>
            <label className="field">
              <span>Name</span>
              <input type="text" value={name} placeholder="my-rover" onChange={(e) => setName(e.target.value)} aria-label="Project name" autoFocus />
              {!valid && name && <span className="small muted">Use letters, digits, ".", "_" or "-", starting with a letter or digit.</span>}
            </label>
            <div className="field" role="radiogroup" aria-label="Template">
              <span>Start from</span>
              <div className="choices">
                {options.map((t) => (
                  <label key={t} className="choice">
                    <input type="radio" name="template" value={t} checked={template === t} onChange={() => setTemplate(t)} />
                    <span className="text"><strong>{TEMPLATES[t]?.label ?? t}</strong>{TEMPLATES[t] && <span>{TEMPLATES[t].description}</span>}</span>
                  </label>
                ))}
              </div>
            </div>
            <div className="field">
              <label className="check"><input type="checkbox" checked={github} onChange={(e) => setGithub(e.target.checked)} />Also create a GitHub repository</label>
              {github && (
                <div className="stack small" style={{ marginLeft: 22, gap: 6 }}>
                  <span className="muted">Owner: the account signed in to <code>gh</code> on the server</span>
                  <div className="row" role="radiogroup" aria-label="Visibility">
                    <label className="check"><input type="radio" name="visibility" checked={isPrivate} onChange={() => setPrivate(true)} />Private</label>
                    <label className="check"><input type="radio" name="visibility" checked={!isPrivate} onChange={() => setPrivate(false)} />Public</label>
                  </div>
                </div>
              )}
            </div>
          </>
        )}
        {create.isError && <ErrorBox error={create.error} />}
      </form>
    </Dialog>
  );
}
