import { lazy, Suspense } from "react";
import { createHashRouter, Navigate, RouterProvider } from "react-router-dom";

import { AppShell, ProjectLayout } from "./components/Layout.tsx";
import { ProjectsPage } from "./pages/ProjectsPage.tsx";
import { OverviewPage } from "./pages/OverviewPage.tsx";
import { ElectricalPage } from "./pages/ElectricalPage.tsx";
import { PcbPage } from "./pages/PcbPage.tsx";
import { SoftwarePage } from "./pages/SoftwarePage.tsx";
import { PartsPage } from "./pages/PartsPage.tsx";
import { AgentPage } from "./pages/AgentPage.tsx";
import { DeployPage } from "./pages/DeployPage.tsx";
import { SettingsPage } from "./pages/SettingsPage.tsx";

// three.js pages load on demand so the project list and tables paint without the 3D bundle.
const ThreeDPage = lazy(() => import("./pages/ThreeDPage.tsx").then((m) => ({ default: m.ThreeDPage })));
const WiringPage = lazy(() => import("./pages/WiringPage.tsx").then((m) => ({ default: m.WiringPage })));
const DiagnosticsPage = lazy(() => import("./pages/DiagnosticsPage.tsx").then((m) => ({ default: m.DiagnosticsPage })));
const SimulationPage = lazy(() => import("./pages/SimulationPage.tsx").then((m) => ({ default: m.SimulationPage })));

const fallback = <div className="skeleton" style={{ margin: 20, height: 400 }} />;
const lazyPage = (node: React.ReactNode) => <Suspense fallback={fallback}>{node}</Suspense>;

// Hash routing: deep links work from any static host, including `r3 serve` without SPA fallback.
const router = createHashRouter([
  {
    element: <AppShell />,
    children: [
      { index: true, element: <ProjectsPage /> },
      { path: "settings", element: <SettingsPage /> },
      {
        path: "p/:pid",
        element: <ProjectLayout />,
        children: [
          { index: true, element: <Navigate to="overview" replace /> },
          { path: "overview", element: <OverviewPage /> },
          { path: "3d", element: lazyPage(<ThreeDPage />) },
          { path: "wiring", element: lazyPage(<WiringPage />) },
          { path: "electrical", element: <ElectricalPage /> },
          { path: "pcb", element: <PcbPage /> },
          { path: "software", element: <SoftwarePage /> },
          { path: "parts", element: <PartsPage /> },
          { path: "sim", element: lazyPage(<SimulationPage />) },
          { path: "diagnostics", element: lazyPage(<DiagnosticsPage />) },
          { path: "agent", element: <AgentPage /> },
          { path: "deploy", element: <DeployPage /> },
          { path: "settings", element: <SettingsPage /> },
        ],
      },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);

export function App() {
  return <RouterProvider router={router} />;
}
