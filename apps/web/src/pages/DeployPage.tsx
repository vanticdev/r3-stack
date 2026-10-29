import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";

import { ApiError, api } from "../api/client.ts";
import { useProject, useSoftware } from "../api/hooks.ts";
import { Icon } from "../components/icons.tsx";
import { Card, Chip, ConfirmDialog, ErrorBox } from "../components/ui.tsx";
import { useRuns } from "../state/runs.tsx";

export function DeployPage() {
  const { pid = "" } = useParams();
  const project = useProject(pid);
  const software = useSoftware(pid);
  const { follow } = useRuns();
  const [target, setTarget] = useState<"sim" | "fleet">("sim");
  const [confirming, setConfirming] = useState(false);
  const [typed, setTyped] = useState("");
  const [force, setForce] = useState(false);
  const deploy = useMutation({
    mutationFn: (confirm: boolean) => api.deploy(pid, { target, confirm, ...(target === "fleet" && force ? { force } : {}) }),
    onSuccess: (result) => { if (result.run_id) follow(pid, result.run_id); },
  });
  const name = project.data?.robot.name ?? pid;
  const ready = project.data?.release.fabrication_ready;
  return (
    <div className="page" style={{ maxWidth: 900 }}>
      <div className="page-head">
        <div className="stack" style={{ gap: 2 }}>
          <h1>Deploy</h1>
          <p>Build the BLITZ firmware bundle and deploy it to the digital twin or to the real fleet.</p>
        </div>
      </div>
      <div className="grid cols-2">
        {(["sim", "fleet"] as const).map((t) => (
          <button key={t} className={`card scenario-card${target === t ? " selected" : ""}`} onClick={() => setTarget(t)} aria-pressed={target === t}>
            <div className="row"><Icon name={t === "sim" ? "play" : "rocket"} /><strong>{t === "sim" ? "Simulation (twin)" : "Fleet (real robots)"}</strong></div>
            <span className="small secondary">
              {t === "sim" ? "Runs the module in the linux/arm64 container against the twin HAL. No hardware is touched."
                : "Syncs the bundle to every discovered target and assigns process plans. Changes real robots; requires confirmation."}
            </span>
          </button>
        ))}
      </div>
      <Card title="Bundle">
        <div className="stack small">
          {(software.data?.modules ?? []).map((m) => <div key={m.name} className="row"><code>{m.name}</code><span className="muted">{m.language} · runs on {m.runs_on} · {m.entrypoint}</span></div>)}
          <div className="row">
            Release gate: {ready ? <Chip tone="good">fabrication ready</Chip> : <Chip tone="warning">not ready — {project.data?.release.counts.failed ?? 0} failed, {project.data?.release.counts.not_run ?? 0} not run</Chip>}
          </div>
        </div>
      </Card>
      {deploy.isError && <ErrorBox error={deploy.error} />}
      {deploy.isSuccess && (
        <Card>
          <div className="stack">
            <div className="row"><Chip tone={deploy.data.ok ? "good" : "critical"}>{deploy.data.ok ? "accepted" : "failed"}</Chip>
              <span>{deploy.data.target === "sim" ? "simulation deploy queued; follow it in the run panel" : `fleet deploy finished (exit ${deploy.data.returncode ?? "?"})`}</span>
              {deploy.data.run_id && <code>{deploy.data.run_id}</code>}</div>
            {(deploy.data.stdout_tail || deploy.data.log) && <pre className="code">{deploy.data.stdout_tail ?? deploy.data.log}</pre>}
            {deploy.data.stderr_tail && <pre className="code">{deploy.data.stderr_tail}</pre>}
          </div>
        </Card>
      )}
      <div className="row">
        <button className={target === "fleet" ? "danger solid" : "primary"} disabled={deploy.isPending} onClick={() => { setTyped(""); setConfirming(true); }}>
          <Icon name="rocket" size={14} />Deploy to {target}
        </button>
        {deploy.error instanceof ApiError && deploy.error.code === "confirmation_required" && <span className="small muted">The API asked for explicit confirmation.</span>}
        {deploy.error instanceof ApiError && deploy.error.code === "validation_required" && target === "fleet" && (
          <label className="check small"><input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />Deploy without a clean validation (force)</label>
        )}
      </div>
      {confirming && (
        <ConfirmDialog title={target === "fleet" ? "Deploy to the real fleet?" : "Deploy to the simulation?"} danger={target === "fleet"}
                       confirmLabel={target === "fleet" ? "Deploy to fleet" : "Deploy"} disabled={target === "fleet" && typed !== name}
                       onCancel={() => setConfirming(false)}
                       onConfirm={() => { setConfirming(false); deploy.mutate(target === "fleet"); }}>
          {target === "fleet" ? (
            <>
              <p>This pushes the firmware bundle to physical robots. {ready ? "" : <strong>The release gate is not satisfied.</strong>}</p>
              <label className="stack" style={{ gap: 4 }}>Type <code>{name}</code> to confirm
                <input type="text" value={typed} onChange={(e) => setTyped(e.target.value)} autoFocus aria-label="Confirm project name" />
              </label>
            </>
          ) : <p>Builds the bundle and starts it against the twin HAL. Nothing physical is touched.</p>}
        </ConfirmDialog>
      )}
    </div>
  );
}
