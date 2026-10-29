import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, api } from "../api/client.ts";
import { useProject, useSoftware } from "../api/hooks.ts";
import type { ProjectDetail } from "../api/types.ts";
import { Markdown } from "../components/Markdown.tsx";
import { Icon } from "../components/icons.tsx";
import { Card, CheckRow, ConfirmDialog, Details, ErrorBox } from "../components/ui.tsx";
import { partLabel, plural, stagePage } from "../lib/labels.ts";
import { itemLabel, itemResult } from "../lib/release.ts";
import { useRuns } from "../state/runs.tsx";

type Target = "sim" | "fleet";

const TARGETS: Record<Target, { label: string; text: string; tech: string }> = {
  sim: {
    label: "Simulation",
    text: "Runs the firmware against the digital twin. No hardware is touched.",
    tech: "BLITZ bundle in a linux/arm64 container against the twin's virtual hardware (HAL), driven by the simulation scenarios.",
  },
  fleet: {
    label: "Physical robots",
    text: "Installs the firmware on the real robots BLITZ finds on the network. Needs the release gate, or an explicit override.",
    tech: "`r3 deploy --target fleet`: BLITZ discovers targets on the local network, syncs the architecture-specific bundle and assigns each robot its process plan.",
  },
};

function bundleRevision(p: ProjectDetail | undefined): string {
  if (!p) return "…";
  const sha = p.fingerprint.robot_yaml_sha256?.slice(0, 8);
  const commit = (p.git?.commit ?? p.fingerprint.git_commit)?.slice(0, 7);
  return [sha && `robot.yaml ${sha}`, commit && `commit ${commit}`].filter(Boolean).join(" · ");
}

export function DeployPage() {
  const { pid = "" } = useParams();
  const project = useProject(pid);
  const software = useSoftware(pid);
  const { follow } = useRuns();
  const [target, setTarget] = useState<Target>("sim");
  const [confirming, setConfirming] = useState(false);
  const [typed, setTyped] = useState("");
  const [force, setForce] = useState(false);
  const deploy = useMutation({
    mutationFn: (confirm: boolean) => api.deploy(pid, { target, confirm, ...(target === "fleet" && force ? { force } : {}) }),
    onSuccess: (result) => { if (result.run_id) follow(pid, result.run_id); },
  });
  const p = project.data;
  const name = p?.robot.name ?? pid;
  const ready = !!p?.release.fabrication_ready;
  const failing = (p?.release.items ?? []).filter((i) => i.status === "failed");
  const unrun = (p?.release.items ?? []).filter((i) => i.status === "not_run" || i.status === "stale");
  const modules = software.data?.modules ?? [];
  const roles = [...new Set(modules.map((m) => m.runs_on ?? "?"))];
  const destination = target === "sim" ? "the digital twin" : `every robot BLITZ discovers on the network (${roles.map(partLabel).join(", ")} role${roles.length === 1 ? "" : "s"})`;
  const blocked = target === "fleet" && !ready && !force;

  return (
    <div className="page" style={{ maxWidth: 920 }}>
      <div className="choices two" role="radiogroup" aria-label="Destination">
        {(["sim", "fleet"] as const).map((t) => (
          <label key={t} className="choice">
            <input type="radio" name="target" checked={target === t} onChange={() => { setTarget(t); deploy.reset(); }} aria-label={TARGETS[t].label} />
            <span className="text"><strong>{TARGETS[t].label}</strong><span>{TARGETS[t].text}</span></span>
          </label>
        ))}
      </div>

      <Card title={target === "sim" ? "Simulation readiness" : "Physical robot readiness"}>
        <div className="check-list" data-testid="deploy-readiness">
          {target === "sim" ? (
            <CheckRow outcome="passed" title="No release gate needed" result="the simulation cannot damage hardware" />
          ) : ready ? (
            <CheckRow outcome="passed" title="Release gate passed" result="every requirement is verified" />
          ) : (
            <>
              <CheckRow outcome="failed" title={`Release gate not passed: ${plural(failing.length, "check")} failed`}>
                {failing.slice(0, 6).map((i) => {
                  const stage = i.evidence[0]?.stage ?? (i.kind === "stage" ? i.subject : null);
                  return (
                    <span key={i.id}>{itemLabel(i)}: {itemResult(i).value}
                      {stage && <> · <Link to={`/p/${encodeURIComponent(pid)}/${stagePage(stage)}`}>open</Link></>}</span>
                  );
                })}
                {failing.length > 6 && <span>and {failing.length - 6} more on the <Link to={`/p/${encodeURIComponent(pid)}/overview`}>Overview</Link></span>}
              </CheckRow>
              {unrun.length > 0 && (
                <CheckRow outcome="not_run" title={`${plural(unrun.length, "check")} not run or outdated`} result="run the pipeline to check them">
                  <span>{unrun.slice(0, 8).map((i) => itemLabel(i)).join(", ")}{unrun.length > 8 ? ", …" : ""}</span>
                </CheckRow>
              )}
              <label className="check override">
                <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
                <span>Deploy anyway (override the release gate). The robots receive firmware whose design checks fail.</span>
              </label>
            </>
          )}
        </div>
      </Card>

      <Card title="Bundle">
        <dl className="kv">
          <dt>Revision</dt><dd>{bundleRevision(p)}</dd>
          <dt>Destination</dt><dd>{target === "sim" ? "Digital twin" : `Robots discovered on the network`}</dd>
          {modules.map((m) => <div key={m.name} style={{ display: "contents" }}><dt>Module</dt><dd>{m.name} · {m.language} on {partLabel(m.runs_on ?? "?")}</dd></div>)}
        </dl>
        <Details summary="How this deploy works"><Markdown className="small">{TARGETS[target].tech}</Markdown></Details>
      </Card>

      {deploy.isError && <ErrorBox error={deploy.error} />}
      {deploy.error instanceof ApiError && deploy.error.code === "validation_required" && !force && (
        <span className="small">The server requires a clean validation for physical robots. Run the pipeline, or tick “Deploy anyway”.</span>
      )}
      {deploy.isSuccess && (
        <Card>
          <div className="stack">
            <strong>{deploy.data.ok ? (deploy.data.target === "sim" ? "Simulation deploy started" : "Fleet deploy finished") : "Deploy failed"}</strong>
            <span className="secondary small">{deploy.data.target === "sim" ? "Follow it from the run status in the header." : `exit code ${deploy.data.returncode ?? "?"}`}</span>
            {(deploy.data.stdout_tail || deploy.data.log) && <pre className="code">{deploy.data.stdout_tail ?? deploy.data.log}</pre>}
            {deploy.data.stderr_tail && <pre className="code">{deploy.data.stderr_tail}</pre>}
          </div>
        </Card>
      )}

      <div className="deploy-action">
        <button className={target === "fleet" ? "danger solid" : "primary"} disabled={deploy.isPending || blocked} onClick={() => { setTyped(""); setConfirming(true); }}>
          <Icon name="rocket" size={14} />{target === "sim" ? "Deploy to simulation" : "Deploy to physical robots"}
        </button>
        {blocked && <span className="small muted">Blocked by the release gate; tick “Deploy anyway” to override.</span>}
      </div>

      {confirming && (
        <ConfirmDialog title={target === "fleet" ? "Deploy to physical robots?" : "Deploy to the simulation?"} danger={target === "fleet"}
                       confirmLabel={target === "fleet" ? "Deploy to robots" : "Deploy to simulation"} disabled={target === "fleet" && typed !== name}
                       onCancel={() => setConfirming(false)}
                       onConfirm={() => { setConfirming(false); deploy.mutate(target === "fleet"); }}>
          <p>Bundle <strong>{bundleRevision(p)}</strong> ({modules.map((m) => m.name).join(", ") || "no modules"}) goes to <strong>{destination}</strong>.</p>
          {target === "fleet" ? (
            <>
              {!ready && <p><strong>The release gate has not passed{force ? "; you chose to override it" : ""}.</strong></p>}
              <label className="stack" style={{ gap: 4 }}>Type <code>{name}</code> to confirm
                <input type="text" value={typed} onChange={(e) => setTyped(e.target.value)} autoFocus aria-label="Confirm project name" />
              </label>
            </>
          ) : <p>Nothing physical is touched.</p>}
        </ConfirmDialog>
      )}
    </div>
  );
}
