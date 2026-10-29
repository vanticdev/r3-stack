# UI review packet (one reviewer per screen)

R3 is "Lovable for robotics": a web app where an engineer, or an AI agent, designs a robot. The
robot is described once in robot.yaml, and R3 builds and checks every domain: CAD (Onshape), PCB
(KiCad), 3D wiring, electrical, simulation and firmware. The target user is a robotics engineer who
wants to see quickly what is wrong and fix it.

Screenshots in this folder: `<screen>_light.png` and `<screen>_dark.png` (full page, 1600 px wide),
and `<screen>_mobile.png` (390 px). They were captured in "mock API" mode: the banner at the top is
dev-only, and the data comes from real build artifacts, some of them stale.

Source (React + TypeScript + three.js): `/Users/godbrigero/.t3/worktrees/r3-stack/t3-46ba1818/apps/web/src/`
- pages: `pages/<Name>Page.tsx`
- shell: `components/Layout.tsx`, `components/ui.tsx`, `styles.css`
- 3D: `viewer/*`
