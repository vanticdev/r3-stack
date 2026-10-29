# syntax=docker/dockerfile:1.7
#
# R3 worker image: runs the whole R3 pipeline headless (servers, GitHub Actions).
# Build from the repository root:
#
#   docker buildx build --platform linux/amd64,linux/arm64 -f docker/r3-worker/Dockerfile -t r3-worker .
#
# See docker/r3-worker/README.md for build arguments, Docker access and KiCad IPC.

ARG UBUNTU_VERSION=24.04
FROM ubuntu:${UBUNTU_VERSION} AS r3-worker

ARG TARGETARCH
# Official KiCad PPA (amd64 + arm64). The kicad/kicad image is amd64-only.
ARG KICAD_PPA=kicad/kicad-10.0-releases
ARG KICAD_PPA_KEY=FDA854F61C4D0D9572BB95E5245D5502FAD7A805
# 1 installs kicad-packages3d (about 3.3 GB) for 3D renders and STEP export with models.
ARG KICAD_3D_MODELS=0
ARG FREEROUTING_VERSION=2.5.0
# Freerouting 2.5 is compiled for Java 25 (class file version 69).
ARG JAVA_PACKAGE=openjdk-25-jre-headless
# Node.js runs the web app's loader tests (apps/web, ESM .js needs Node >= 22.7).
ARG NODE_VERSION=24.22.2

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/r3/venv \
    PATH=/opt/r3/venv/bin:$PATH \
    # Headless rendering: r3 picks MUJOCO_GL itself (r3_sim.headless): EGL when a
    # GPU is passed through (/dev/dri, /dev/nvidia0), else OSMesa. Set MUJOCO_GL
    # (and PYOPENGL_PLATFORM) to force one. matplotlib always uses Agg.
    MPLBACKEND=Agg \
    # One BLAS thread: the co-simulation solves many tiny systems per step, and
    # OpenBLAS's thread pool made a 12 s scenario take >10x longer on 16 cores.
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    QT_QPA_PLATFORM=offscreen \
    R3_FREEROUTING_JAR=/opt/freerouting/freerouting.jar \
    R3_WORKER_IMAGE=1

# --------------------------------------------------------------- system tools
# Headless GL (EGL + OSMesa), ngspice (binary + libngspice), Java, Xvfb for the
# KiCad IPC API, ffmpeg for sim clips, tini as PID 1.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg git make tini unzip xz-utils libatomic1 \
      python3 python3-venv python3-dev \
      libegl1 libegl-mesa0 libgl1 libgl1-mesa-dri libglx-mesa0 libgles2 libopengl0 libosmesa6 \
      ngspice libngspice0 libngspice0-dev \
      ${JAVA_PACKAGE} \
      xvfb xauth \
      ffmpeg

# ------------------------------------------------------------------ Docker CLI
# Client only: firmware containers run on the host daemon (mounted socket) or a
# docker:dind sidecar (DOCKER_HOST). See README.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc \
 && . /etc/os-release \
 && echo "deb [arch=${TARGETARCH} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
      > /etc/apt/sources.list.d/docker.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends docker-ce-cli docker-buildx-plugin

# ---------------------------------------------------------------------- KiCad
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    curl -fsSL "https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x${KICAD_PPA_KEY}" \
      | gpg --dearmor -o /etc/apt/keyrings/kicad.gpg \
 && . /etc/os-release \
 && echo "deb [signed-by=/etc/apt/keyrings/kicad.gpg] https://ppa.launchpadcontent.net/${KICAD_PPA}/ubuntu ${VERSION_CODENAME} main" \
      > /etc/apt/sources.list.d/kicad.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      kicad kicad-symbols kicad-footprints kicad-templates \
      $( [ "${KICAD_3D_MODELS}" = "1" ] && echo kicad-packages3d ) \
 && kicad-cli version

COPY docker/r3-worker/r3-kicad-headless /usr/local/bin/r3-kicad-headless

# ---------------------------------------------------------------- Freerouting
# The release jar runs in CLI mode (--gui.enabled=false) on the headless JRE.
RUN mkdir -p /opt/freerouting \
 && curl -fsSL -o /opt/freerouting/freerouting.jar \
      "https://github.com/freerouting/freerouting/releases/download/v${FREEROUTING_VERSION}/freerouting-${FREEROUTING_VERSION}.jar" \
 && echo "${FREEROUTING_VERSION}" > /opt/freerouting/VERSION \
 && printf '#!/bin/sh\nexec java -Djava.awt.headless=true -jar "${R3_FREEROUTING_JAR:-/opt/freerouting/freerouting.jar}" --gui.enabled=false "$@"\n' \
      > /usr/local/bin/freerouting \
 && chmod 0755 /usr/local/bin/freerouting /usr/local/bin/r3-kicad-headless

# -------------------------------------------------------------------- Node.js
RUN case "${TARGETARCH}" in amd64) node_arch=x64 ;; arm64) node_arch=arm64 ;; *) echo "unsupported ${TARGETARCH}"; exit 1 ;; esac \
 && curl -fsSL -o /tmp/node.tar.xz "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${node_arch}.tar.xz" \
 && curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt" | grep " node-v${NODE_VERSION}-linux-${node_arch}.tar.xz\$" \
      | sed "s# node-v.*# /tmp/node.tar.xz#" | sha256sum -c - \
 && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 --exclude='*/include' --exclude='*/share/doc' \
 && rm /tmp/node.tar.xz \
 && node --version

# --------------------------------------------------------------------- Python
COPY docker/r3-worker/requirements.txt /opt/r3/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    python3 -m venv "$VIRTUAL_ENV" \
 && pip install --upgrade pip setuptools wheel \
 && pip install -r /opt/r3/requirements.txt

# ------------------------------------------------- optional: Sionna RT (CPU)
# OPTIONAL LAYER, off by default. Sionna RT ray tracing for the radio model runs
# on CPU through Dr.Jit's LLVM backend (no GPU needed). Enable with
# --build-arg WITH_SIONNA=1; the radio stage uses it when importable and falls
# back to its analytic link model otherwise.
ARG WITH_SIONNA=0
ARG SIONNA_RT_VERSION=2.2.0
ENV DRJIT_LIBLLVM_PATH=/usr/local/lib/libLLVM.so
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    --mount=type=cache,target=/root/.cache/pip \
    if [ "${WITH_SIONNA}" = "1" ]; then \
      apt-get update \
   && apt-get install -y --no-install-recommends libllvm18 \
   && ln -sf "$(ls /usr/lib/llvm-18/lib/libLLVM.so.1 /usr/lib/*-linux-gnu/libLLVM-18.so* 2>/dev/null | head -n1)" "$DRJIT_LIBLLVM_PATH" \
   && pip install "sionna-rt==${SIONNA_RT_VERSION}" \
   && python -c "import drjit, mitsuba; mitsuba.set_variant('llvm_ad_mono_polarized'); print('sionna-rt LLVM backend OK')"; \
    fi

# --------------------------------------------------------------- r3-platform
# Editable install, so the source tree (examples/, templates) stays addressable.
COPY . /opt/r3/platform
RUN pip install --no-deps -e /opt/r3/platform \
 && r3 --help > /dev/null

WORKDIR /work
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["r3", "doctor"]
