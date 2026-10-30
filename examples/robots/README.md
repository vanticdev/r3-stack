# Robot projects

This directory is an initially empty project root for `r3 serve --projects-root examples/robots`.
Only subdirectories containing `robot.yaml` appear in the project list. This README does not create a project.

Create a project from nothing using `r3 create <name> --template blank`, then add components,
interfaces, wiring, firmware, and validation scenarios. See [build a robot](../../docs/guide/build-a-robot.md).
Keep generated meshes, recordings, caches, and build outputs out of tracked projects.
Deployable modules belong in the project’s `backend/<language>/<module-folder>` directories.
