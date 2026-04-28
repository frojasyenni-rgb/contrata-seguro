# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**contrata-seguro** is an Argentine legal research assistant that searches labor (SCBA) and federal (PJN) judicial databases. Users authenticate via Supabase, consume search credits, and pay via MercadoPago. The scraper runs as a subprocess and streams results back via SSE.

## Tech Stack

- **Backend**: Python 3.13 + Flask, deployed on Railway via Gunicorn
- **Frontend**: Single-page app in `static/index.html` (vanilla JS + inline CSS, no build step)
- **Auth & DB**: Supabase (JWT auth, RLS policies, profiles/queries/payments tables)
- **Payments**: MercadoPago SDK
- **Scraping**: `requests` + `BeautifulSoup4` for SCBA/MEV; Playwright for PJN browser automation
- **OCR**: PyTesseract + EasyOCR (for CAPTCHA solving)

## Running Locally

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Start dev server (reads .env automatically)
python api.py

# Run with Gunicorn (production mode)
python entrypoint_gunicorn.py
```

The app serves on `http://localhost:5000` by default. The `PORT` env var overrides this.

## Running Tests

```bash
# All tests
python -m pytest tests/

# Single test file
python -m pytest tests/test_cuitonline_lookup.py -v
```

## Required Environment Variables

```
SUPABASE_URL=
SUPABASE_SERVICE_KEY=
MP_ACCESS_TOKEN=
MP_WEBHOOK_SECRET=
APP_URL=https://contrataseguro.ar
SCBA_USUARIO=
SCBA_PASSWORD=
PORT=
WA_INTERNO_NUM=
ADMIN_TOKEN=
TURNSTILE_SITE_KEY=       # Cloudflare Turnstile (optional)
TURNSTILE_SECRET_KEY=
OPENAI_API_KEY=           # Whisper audio transcription for PJN captcha (optional, falls back to OCR)
```

## Architecture & Data Flow

### Components

| File | Role |
|------|------|
| `api.py` | Flask app — all routes, SSE streaming, Supabase/MP integration |
| `buscar_simple.py` | CLI scraper — runs as subprocess, emits `PROGRESO:` and `RESULTADO:` JSON lines to stdout |
| `pjn_session.py` | HTTP session manager for PJN with CAPTCHA handling |
| `pjn_playwright.py` | Playwright-based browser automation fallback for PJN |
| `cuitonline_lookup.py` | Resolves CUIL/CUIT codes to person names via cuitonline.com |
| `entrypoint_gunicorn.py` | Reads `PORT` from env, starts single-worker Gunicorn |
| `static/index.html` | Full SPA — auth, search UI, payment flow, history |
| `static/pjn_captcha_embed.html` | CAPTCHA widget iframe for PJN |

### Search Flow (SSE)

```
Browser
  → GET /buscar/stream?nombre=...&token=<supabase_jwt>
  → Flask validates token via Supabase, checks credits
  → subprocess(buscar_simple.py --nombre ...) spawned
  → Scraper emits: PROGRESO: {"mensaje": "..."} / RESULTADO: {"expedientes": [...]}
  → Flask pipes lines as SSE events to browser
  → On completion, Flask deducts credit and saves query to Supabase
```

### PJN CAPTCHA Flow

PJN requires solving a CAPTCHA before each search:
1. `POST /pjn/prepare` — initializes a session and fetches the CAPTCHA widget
2. Frontend renders the widget in an iframe (`pjn_captcha_embed.html`)
3. `POST /pjn/verify` — submits the solved token; backend stores session keyed to user
4. `GET /buscar/stream` — reuses the PJN session within its TTL

## Key API Routes

| Route | Purpose |
|-------|---------|
| `GET /buscar/stream` | SSE search endpoint (main user-facing search) |
| `POST /pjn/prepare` | Initialize PJN CAPTCHA session |
| `POST /pjn/verify` | Submit solved CAPTCHA token |
| `GET /pjn/captcha-init.js` | Proxy for PJN widget JS (rewrites URLs) |
| `GET/PUT /perfil` | User profile and credit balance |
| `POST /pagar` | Create MercadoPago payment preference |
| `POST /webhook/mp` | MercadoPago webhook (adds credits on payment) |
| `GET /debug-scraper` | Debug endpoint — **disable in production** |

## Deployment

Deployed on Railway. Configuration lives in `railway.toml` and `nixpacks.toml`. The nixpacks build installs `tesseract-ocr` system package and runs `playwright install`. Secrets are set as Railway environment variables, not in `.env`.
