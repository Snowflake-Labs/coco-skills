# Build only from a reviewed checkout. Never use a candidate skill as build context.
FROM python:3.11.14-slim-bookworm@sha256:65a93d69fa75478d554f4ad27c85c1e69fa184956261b4301ebaf6dbb0a3543d
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /validator
# Synthetic CI-selection tests need real local repositories, not Git mocks alone.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt
# Snapshots keep files private (0600); runtime ownership must make them readable.
# Preserve script-relative policy resources as well as the Python modules.
COPY --chown=65532:65532 scripts/ ./
COPY --chown=65532:65532 references/catalog-snapshot.json ./catalog-snapshot.json
USER 65532:65532
ENTRYPOINT ["python", "-I", "/validator/entrypoint.py"]
