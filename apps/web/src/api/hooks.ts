import { useQuery, type QueryClient } from "@tanstack/react-query";

import { api } from "./client.ts";

/** Query keys. Everything under ["project", pid] is invalidated when a run or agent session finishes. */
export const keys = {
  doctor: ["doctor"] as const,
  projects: ["projects"] as const,
  project: (pid: string) => ["project", pid] as const,
  section: (pid: string, section: string, ...rest: (string | number)[]) => ["project", pid, section, ...rest] as const,
};

export const useDoctor = () => useQuery({ queryKey: keys.doctor, queryFn: ({ signal }) => api.doctor(signal), staleTime: 60_000 });
export const useProjects = () => useQuery({ queryKey: keys.projects, queryFn: ({ signal }) => api.projects(signal) });
export const useProject = (pid: string) =>
  useQuery({ queryKey: keys.project(pid), queryFn: ({ signal }) => api.project(pid, signal) });
export const useSpec = (pid: string) =>
  useQuery({ queryKey: keys.section(pid, "spec"), queryFn: ({ signal }) => api.spec(pid, signal) });
export const useCatalog = (pid: string) =>
  useQuery({ queryKey: keys.section(pid, "catalog"), queryFn: ({ signal }) => api.catalog(pid, signal), staleTime: 300_000 });
export const useModel = (pid: string) =>
  useQuery({ queryKey: keys.section(pid, "model"), queryFn: ({ signal }) => api.model(pid, signal) });
export const useCad = (pid: string) => useQuery({ queryKey: keys.section(pid, "cad"), queryFn: ({ signal }) => api.cad(pid, signal) });
export const useWiring = (pid: string) =>
  useQuery({ queryKey: keys.section(pid, "wiring"), queryFn: ({ signal }) => api.wiring(pid, signal) });
export const useElectrical = (pid: string) =>
  useQuery({ queryKey: keys.section(pid, "electrical"), queryFn: ({ signal }) => api.electrical(pid, signal) });
export const usePcb = (pid: string) => useQuery({ queryKey: keys.section(pid, "pcb"), queryFn: ({ signal }) => api.pcb(pid, signal) });
export const useSoftware = (pid: string) =>
  useQuery({ queryKey: keys.section(pid, "software"), queryFn: ({ signal }) => api.software(pid, signal) });
export const useScenarios = (pid: string) =>
  useQuery({ queryKey: keys.section(pid, "sim"), queryFn: ({ signal }) => api.scenarios(pid, signal) });
export const useScenario = (pid: string, scenario: string | null, hz = 30) =>
  useQuery({
    queryKey: keys.section(pid, "sim", scenario ?? "", hz),
    queryFn: ({ signal }) => api.scenario(pid, scenario!, hz, signal),
    enabled: !!scenario,
    staleTime: 300_000,
  });
export const useDiagnostics = (pid: string) =>
  useQuery({ queryKey: keys.section(pid, "diagnostics"), queryFn: ({ signal }) => api.diagnostics(pid, signal) });

export function invalidateProject(client: QueryClient, pid: string): Promise<void> {
  return Promise.all([
    client.invalidateQueries({ queryKey: keys.project(pid) }),
    client.invalidateQueries({ queryKey: keys.projects }),
  ]).then(() => undefined);
}
