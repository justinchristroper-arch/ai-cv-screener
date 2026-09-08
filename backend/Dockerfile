# Backend image.
#
# Two stages, for one reason: the build stage needs a compiler toolchain for any
# wheel that has no binary for this platform, and the runtime stage should not
# ship one. Everything installed lands in a virtualenv that is copied across
# whole, so the final image contains the dependencies and nothing that built
# them.
#
# The Python version is pinned to the same minor this project is developed and
# CI-tested on (see .python-version). "latest" in a base image is a build that
# changes under you.

# --------------------------------------------------------------------- build
FROM python:3.10-slim-bookworm AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# The lock file, not the range file: an image is a build artefact, and it should
# be reproducible. requirements.txt declares what is compatible; this records
# what was actually verified.
COPY requirements.lock.txt /tmp/requirements.lock.txt
RUN pip install --no-cache-dir -r /tmp/requirements.lock.txt

# ------------------------------------------------------------------- runtime
FROM python:3.10-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# A non-root user, created before anything is copied so the ownership is right
# the first time. Nothing in this application needs root, and a container that
# runs as root is one container-escape bug away from being a host problem.
RUN useradd --create-home --uid 10001 screener

COPY --from=build /opt/venv /opt/venv

WORKDIR /app
COPY --chown=screener:screener . /app

# Uploaded CVs are personal data and must not live in the image layer. This is
# the default UPLOAD_STORAGE_DIR inside the container; mount a volume over it in
# any deployment where the files need to survive a restart.
RUN mkdir -p /var/lib/ai-cv-screener/uploads \
    && chown -R screener:screener /var/lib/ai-cv-screener
ENV UPLOAD_STORAGE_DIR=/var/lib/ai-cv-screener/uploads

USER screener

EXPOSE 8000

# Liveness only. /health touches no external system on purpose, so a database
# outage cannot make the process look dead to an orchestrator and get it killed
# during exactly the incident where its logs are most useful.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

# Migrations are NOT run here. Applying schema changes from every starting
# replica is a race; run `alembic upgrade head` as a separate step before
# rolling out (docs/deployment.md).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
