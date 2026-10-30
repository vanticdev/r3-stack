# Hosted platform plan

**Status: future plan. None of this is built, and none of it is part of the current proof-of-concept run.**
Today R3 runs as a local `r3 serve` plus the web app, as described in
[the pipeline](r3-pipeline.md). Do not change the pipeline toward this plan until it is scheduled.

## Goal

A whole robotics project lives in one monorepo and can be built autonomously, ideally by command or by an
AI agent. Design work happens on the user's machine. Fast tests run locally. A hosted service checks that
the design is viable and returns detailed diagnostics that a person or an agent can act on.

## Pieces

| Piece | Runs on | Owns |
| --- | --- | --- |
| Website | Its own server | The UI and delegation: projects, test statistics, 3D model, wiring, sending work to sim servers |
| Sim server | Containerized; Docker Compose for now | Receives a full project mirror, builds and compiles it, runs the full model, returns diagnostics |
| Local tooling | The user's machine | Onshape integration, KiCad, code, agent tools, local MuJoCo tests |

The UI does not live on the client. It is a separate hosted website that acts as a delegator, like a normal
web service.

## Two model versions

Both come from the same assembly, so they cannot drift apart. Optional overrides are allowed on the simplified
model.

1. **Simplified:** joints with specific values, motor specs and other relevant specs, the kind of model usually
   written by hand for MuJoCo (for example a robot dog). No extra simulation layers.
2. **Full:** a complete compilation of the model. It is not meant to run on a client. The user cannot choose which
   parts or joints are included.

The server can report where the two disagree, which shows how far local results can be trusted.

## Two kinds of simulation

- **Local:** MuJoCo on the user's machine, with test code through BLITZ. Assume a single process for now, or a
  replaceable communication layer. Do not use Autobahn for this. No Gazebo.
- **Server:** the full model, run in the cloud.

The server is deliberately narrow. **It tests the viability of the design, CAD and electronics, not more.** Keep the
cloud tests to the bare minimum, basically verifying that the model stays together properly when in motion.
Other tests, including most software behaviour, stay local. Some tests are not suited to the server at all.

## Tests

Tests are user-defined or AI-defined, in a framework modelled on the drone project's: a `sim` fixture, a world picked
per test, and an instance count such as `--sim-instances N`. A test receives its environment from the framework.
R3 also ships a small set of mandatory tests. Only server results count for release.

## CAD and PCB sources

- **CAD comes from Onshape only.** Any change to the CAD must come from Onshape. Neither agents nor people make local
  edits to the model. Changes go through the Onshape integration and are pulled down. This is strict and necessary.
- The repo holds a pulled snapshot with its Onshape version and hash, so a local edit fails with an error.
- Offline recomposition can only be a preview. It never counts as evidence.
- KiCad runs on the user's machine, so edits can happen locally; the same discipline applies where it fits.

**Onshape rate limit:** every CAD change uses the per-user limit on Part Studio feature calls, which has been observed
to apply across API keys, headless sessions and the browser extension alike. Plan around it: smaller incremental
updates, batching parameter edits where the API allows it, and reusing unchanged parts. A paid plan or an agreement
with Onshape may raise it.

## Monorepo

One repo per robot, holding all of its sources: CAD snapshots, PCB, firmware and tests. R3 is a pinned dependency,
installed and resolved the way `node_modules` is, not a copy committed into the project. BLITZ must be packaged as part
of that dependency, because real-robot deployment needs it.

## Running a job

1. The user's machine pulls the CAD from Onshape and uploads a full mirror of the project.
2. The server builds, compiles and runs it, then returns diagnostics saying what is wrong and what must change.
3. Results are pinned to the git commit, Onshape version and KiCad hash, and shown as history per project.

Servers can be brought up or down. When a server is free, the next user can deploy to it, with nothing left running
from the previous user. An available server is a free slot in a pool.

## First server scope

- One machine, Docker Compose, no user accounts.
- The server has no Onshape access for now; it may later.
- Uploads are the whole project for now. Avoiding large uploads is a later problem.

## Later

- Treat uploaded firmware as untrusted: throwaway containers, no network, resource limits.
- Avoid re-uploading large models, for example by content hash or by fetching from Onshape on the server.
- Decide the communication layer between firmware and local simulation. `r3_hal` is the likely interface, with an
  in-process version allowing lockstep, repeatable tests.
- User accounts and multiple machines.
