# CentrAlign worker: console + API + the two mock apps, with a real Chromium for browser execution.
# Long-running runs and a browser can't live in a Vercel function, so this runs as a container (Render).
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && playwright install --with-deps chromium
COPY src src
COPY mock_apps mock_apps
ENV RUNS_DIR=/tmp/runs ERP_DATABASE_URL=sqlite:////tmp/runs/erp.db
CMD ["sh", "-c", "export PORTAL_URL=http://127.0.0.1:${PORT:-8000}/portal ERP_URL=http://127.0.0.1:${PORT:-8000}/erp; exec uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
