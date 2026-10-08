# Deployment

## Topology
| Part | Where | Why |
|---|---|---|
| Landing page (`web/`) + demo video | Vercel (static) | CDN, a `vercel.app` address |
| Worker: console, API, runtime, Chromium, mock portal, mock ERP | Render, Docker (`Dockerfile`, `render.yaml`) | runs last minutes and need a real browser; a serverless function can't hold either |
| Routing | `web/vercel.json` rewrites `/console`, `/api/*`, `/portal/*`, `/erp/*` to the Render service | one origin for the evaluator |
| LLM | Groq / Gemini / OpenAI APIs | keys are Render secrets |

The container serves the mock apps itself and the worker reaches them over `127.0.0.1:$PORT`, so the browser
automation never leaves the box. State lives in `/tmp/runs` (SQLite): a Render free instance restarting or
spinning down resets the demo, which is acceptable for a demo and documented.

## Render
1. Dashboard → New → Blueprint → this repository (reads `render.yaml`).
2. Fill `GROQ_API_KEY` (required), `GEMINI_API_KEY`, `OPENAI_API_KEY` (optional fallbacks). `ERP_API_KEY` and
   `PORTAL_API_KEY` are generated.
3. Wait for the build (Chromium install takes a few minutes) and open `/healthz`.

## Vercel
`web/` is deployed as a static project; `web/vercel.json` holds the rewrites to the Render URL.

## Running locally
See the README Quick Start.
