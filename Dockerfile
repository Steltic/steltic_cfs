# Cloud Run image for the Steltic CFS demo (`cfs-app`) — the FastAPI app + the CFS engine
# in ONE container, mirroring the AISC `steel-app` image (steel_by_webpage/Dockerfile).
# On Cloud Run the agent's run_python runs as an in-container SUBPROCESS (EXECUTOR=subprocess),
# because Cloud Run can't do Docker-in-Docker — so this image carries BOTH the web/app deps and
# the engine runtime (the subprocess shares this interpreter via PYTHONPATH=steel_engine).
# openseespy is included for the engine's dual-path checks; the CFS solvers themselves are
# pure python (cfs_frame / static_model), so runs succeed either way.
FROM python:3.12-slim

ARG DEBIAN_FRONTEND=noninteractive
ENV PIP_ROOT_USER_ACTION=ignore \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# native libs openseespy + matplotlib need at runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 libglib2.0-0 libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# Unprivileged uid the agent's run_python drops to (steltic/sandbox/subprocess_exec.py — ported
# from the AISC wrapper 2026-08-12). The app itself stays root so it CAN drop; model code never
# runs as root. SANDBOX_UID below must match this uid exactly.
RUN useradd --no-create-home --shell /usr/sbin/nologin --uid 1001 sandbox \
 && [ "$(id -u sandbox)" = "1001" ]

# web/app deps + engine runtime, from pyproject [project] dependencies
RUN pip install --no-cache-dir \
        "fastapi>=0.110" "uvicorn[standard]>=0.27" "httpx>=0.27" \
        "itsdangerous>=2.1" "python-multipart>=0.0.9" \
        openseespy numpy scipy matplotlib

# app code + engine + everything config.py / the served page / the demo briefs read at runtime
COPY steltic/        ./steltic/
COPY steel_engine/   ./steel_engine/
COPY frontend/       ./frontend/
COPY contract/       ./contract/
COPY test_buildings/ ./test_buildings/
COPY DISCLAIMER.md   ./
COPY PRIVACY.md      ./
COPY README.md       ./

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/mpl \
    EXECUTOR=subprocess \
    DATA_DIR=/tmp/data \
    SANDBOX_UID=1001 \
    SANDBOX_GID=1001 \
    SANDBOX_RLIMIT_AS_GB=2

# DEMO=1 and the demo caps are set at DEPLOY time (gcloud --set-env-vars), not baked in,
# so the same image can be run locally unrestricted with DEMO=0.

# Cloud Run injects $PORT (default 8080); bind to it.
CMD ["sh", "-c", "exec uvicorn steltic.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
