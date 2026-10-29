import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api/client.ts";
import { keys, useProjects } from "../api/hooks.ts";
import { Icon } from "../components/icons.tsx";
import { Card, Chip, ConfirmDialog, Empty, ErrorBox, QueryView, StageChip } from "../components/ui.tsx";
import { timeAgo } from "../lib/units.ts";

export function ProjectsPage() {
  const projects = useProjects();
  return (
    <div className="content">
      <div className="page">
        <div className="page-head">
          <div className="stack" style={{ gap: 2 }}>
            <h1>Projects</h1>
            <p>Robots described once in <code>robot.yaml</code>; every domain is built and checked from it.</p>
          </div>
        </div>
        <div className="grid side">
          <QueryView query={projects} height={200}>
            {({ projects: list }) => list.length ? (
              <div className="grid cols-2">
                {list.map((p) => (
                  <Link key={p.id} to={`/p/${encodeURIComponent(p.id)}/overview`} className="card project-card" data-testid="project-card">
                    <div className="row">
                      <span className="brand-mark" style={{ width: 30, height: 30 }}><Icon name="cube" size={16} /></span>
                      <div className="stack" style={{ gap: 0 }}>
                        <h2>{p.name}</h2>
                        <span className="muted small mono ellipsis" title={p.path}>{p.id}{p.version ? ` · v${p.version}` : ""}</span>
                      </div>
                    </div>
                    {p.description && <p className="secondary">{p.description}</p>}
                    {p.error && <span className="small" style={{ color: "var(--critical)" }}>{p.error}</span>}
                    <div className="row small">
                      {Object.entries(p.stages).filter(([, v]) => v !== "missing").map(([stage, status]) => <StageChip key={stage} stage={stage} status={status} />)}
                    </div>
                    <div className="row small">
                      {p.last_run?.status === "ok" && <Chip tone="good">checks pass</Chip>}
                      {p.last_run?.status === "failed" && <Chip tone="critical">{p.last_run.counts?.error ?? 0} errors</Chip>}
                      {!!p.last_run?.counts?.warning && <Chip tone="warning">{p.last_run.counts.warning} warnings</Chip>}
                      {p.last_run?.stale && <Chip tone="stale">stale results</Chip>}
                      <span className="muted">last run {p.last_run ? timeAgo(p.last_run.finished_at) : "never"}</span>
                    </div>
                  </Link>
                ))}
              </div>
            ) : <Card><Empty title="No projects yet">Create one from a template.</Empty></Card>}
          </QueryView>
          <CreateProject templates={projects.data?.templates ?? []} />
        </div>
      </div>
    </div>
  );
}

function CreateProject({ templates }: { templates: string[] }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [template, setTemplate] = useState("rc-car");
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
  const options = [...new Set(["rc-car", "blank", ...templates])];
  const valid = /^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(name);
  return (
    <Card title="New project" subtitle="Scaffold from a template (r3_github)">
      <form className="stack" onSubmit={(e) => { e.preventDefault(); if (github) setConfirming(true); else create.mutate(); }}>
        <label className="stack" style={{ gap: 4 }}>
          <span className="small secondary">Name</span>
          <input type="text" value={name} placeholder="my-rover" onChange={(e) => setName(e.target.value)} aria-label="Project name" />
        </label>
        <label className="stack" style={{ gap: 4 }}>
          <span className="small secondary">Template</span>
          <select value={template} onChange={(e) => setTemplate(e.target.value)} aria-label="Template">
            {options.map((t) => <option key={t}>{t}</option>)}
          </select>
        </label>
        <label className="check"><input type="checkbox" checked={github} onChange={(e) => setGithub(e.target.checked)} />Also create a GitHub repository</label>
        {github && <label className="check" style={{ marginLeft: 22 }}><input type="checkbox" checked={isPrivate} onChange={(e) => setPrivate(e.target.checked)} />Private</label>}
        {create.isError && <ErrorBox error={create.error} />}
        <div className="row"><button className="primary" type="submit" disabled={!valid || create.isPending}><Icon name="plus" size={14} />Create</button>
          {!valid && name && <span className="small muted">letters, digits, ".", "_" or "-"</span>}</div>
      </form>
      {confirming && (
        <ConfirmDialog title="Create a GitHub repository?" confirmLabel="Create repository" onCancel={() => setConfirming(false)}
                       onConfirm={() => { setConfirming(false); create.mutate(); }}>
          <p>This creates <strong>{isPrivate ? "a private" : "a public"}</strong> repository named <code>{name}</code> on GitHub with the <code>gh</code> CLI and pushes the scaffold.</p>
        </ConfirmDialog>
      )}
    </Card>
  );
}
