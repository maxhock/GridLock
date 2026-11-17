# Built from the repository root: the image runs the grid federate's own code
# and pins, so the power flow cannot drift from what federates/grid solves.
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN mkdir -p /data /app/meta_store

COPY federates/grid/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY federates/grid/main.py grid_federate.py
COPY federates/grid_reinforce/reinforce.py federates/grid_reinforce/main.py ./

# --- Test stage ---
FROM base AS test
RUN pip install --no-cache-dir pytest pytest-mock
COPY federates/grid_reinforce/test_main.py .
RUN pytest --maxfail=1 --disable-warnings -v

# --- Final image ---
FROM base AS final
CMD ["python", "main.py"]

# --- Usage (from the repository root) ---
# docker build -f federates/grid_reinforce/Dockerfile --target test -t grid_reinforce-test .
# docker build -f federates/grid_reinforce/Dockerfile --target final -t grid_reinforce .
