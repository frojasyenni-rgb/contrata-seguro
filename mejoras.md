# Análisis arquitectónico y mejoras propuestas

Aquí va el análisis con el repo revisado (código en `contrata-seguro/`: `index.html`, `api.py`, `buscar_simple.py`, `entrypoint_gunicorn.py`, `railway.toml`, `nixpacks.toml`, `Procfile`, `Dockerfile`, `requirements.txt`, `CNAME`, `.gitignore`). No hay carpeta `.github/` ni workflows de Pages en el repo.

---

## 1. Arquitectura general

**Frontend**
- Un solo artefacto: `index.html` (HTML + CSS + JS inline).
- Dependencias por CDN: `@supabase/supabase-js`, SDK MercadoPago, Google Fonts.
- No hay build (Vite/Webpack), ni bundle, ni tests frontend en el repo.

**Backend**
- `api.py`: Flask, CORS, rutas REST/SSE, Supabase (Python), MercadoPago, subprocess al scraper.
- `buscar_simple.py`: scraping SCBA + PJN, progreso por stdout, resultado final en línea `RESULTADO:...`.

**Despliegue inferido**
- **Railway**: `railway.toml`, `nixpacks.toml`, `Procfile`, `Dockerfile`, `entrypoint_gunicorn.py` (Gunicorn -> `api:app`). El propio backend sirve `index.html` en `GET /` (comentario en código: sitio en Railway).
- **GitHub Pages / dominio**: `CNAME` con `contrataseguro.ar` suele usarse para Pages, pero **en el repo no hay** configuración de GitHub Actions ni `docs/` separado: el “deploy Pages” queda fuera del código (manual o en otro repo).

**¿Estática (Pages) + API (Railway)?**
El frontend asume `API_URL = window.location.origin` (mismo origen que la página). Eso encaja si **todo** (HTML + API) corre en el **mismo host** (p. ej. Railway con dominio custom). Si en producción el HTML vive en **GitHub Pages** y la API en **otro host** (p. ej. `*.up.railway.app`), **las llamadas a `/buscar/stream` y `/pagar` fallarían** sin cambiar `API_URL` o un proxy inverso bajo el mismo dominio.

**Acoplamientos y diseño**
- **Doble vía de datos**: historial y perfil en parte por **Supabase desde el navegador** (`sb.from('consultas')`, `perfiles`), y búsqueda/pagos por **Flask**. Reglas de negocio (créditos, planes) repartidas entre RLS (si existe), cliente y servidor -> riesgo de inconsistencias y más superficie de auditoría.
- **Scraper como subprocess + archivo compartido**: la API lee `resultado.json` como respaldo; el scraper actual **no escribe** ese archivo (solo imprime JSON). El camino `/buscar` depende de un archivo que hoy no genera el script -> acoplamiento frágil/legacy.
- **Bug en `POST/GET /buscar`**: se invoca `nombre.upper().split()` como argumentos del script, así “GARCIA JUAN” se convierte en `argv[1]=GARCIA`, `argv[2]=JUAN` (el segundo token pasa a “DNI” en el scraper). El streaming usa un solo argumento de nombre; **POST/GET `/buscar` no es equivalente** al flujo principal.

---

## 2. Flujo de datos

**Navegador -> resultado**
1. Auth y perfil: Supabase JS -> Supabase HTTP (sesión, `perfiles`, en registro también `upsert` desde el cliente).
2. Búsqueda: `EventSource` a `GET /buscar/stream?nombre=...&token=...` (mismo `origin` que la página).
3. Backend: hilo + `subprocess` ejecuta `buscar_simple.py`, parsea `PROGRESO:` / `RESULTADO:` y emite SSE.
4. Tras resultado: `guardar_consulta` en Supabase (servidor) y actualización de créditos según perfil.
5. Panel: el historial se carga **otra vez desde el cliente** con `sb.from('consultas')`, no desde `GET /consultas` del Flask (endpoint definido pero no usado en el HTML revisado).

**Endpoints relevantes** (Flask)
`/`, `/api/info`, `/health`, `/buscar/stream`, `/buscar`, `/resolver-pjn`, `/consultas`, `GET|PUT /perfil`, `/pagar`, `/webhook/mp`, `/login`, `/debug-scraper`.

**CORS**
`CORS(app, origins=["*"])` elimina fricción entre orígenes distintos, pero en un escenario multi-dominio es permisivo al máximo; combinado con credenciales en query string aumenta el riesgo operativo (logs, referrers).

**Errores**
- Muchos `except: pass` en auth de streaming (fallo silencioso -> búsqueda sin usuario/créditos como anónimo).
- Webhook MP devuelve `{"ok":true}` en casos dudosos sin trazabilidad clara.
- Frontend: `evtSource.onmessage` con `catch(err){}` vacío oculta errores de parseo.

---

## 3. Configuración de deploy

- **Railway**: start command unificado vía `entrypoint_gunicorn.py`; `PORT` bien manejado; Gunicorn `--timeout 300`, **1 worker** (explícito límite de concurrencia).
- **GitHub Pages**: solo `CNAME`; sin `jekyll`, sin workflow; el README casi vacío no documenta el flujo.
- **Dockerfile**: Python 3.13-slim; coherente con `requirements.txt`.
- **nixpacks**: instala `mercadopago` dos veces (ya está en `requirements.txt`) -> redundante pero no grave.

**URLs y entorno**
- No hay `localhost` hardcodeado en el código de app (bien).
- `APP_URL` default `https://contrataseguro.ar`: debe coincidir con **dónde vive el webhook** `/webhook/mp`. Si el dominio apunta a Pages y la API solo a Railway, **MercadoPago no llegará al backend** salvo subdominio tipo `api.*` o proxy.
- Variables esperadas (inferidas): `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `MP_ACCESS_TOKEN`, `APP_URL`, `ADMIN_TOKEN`, `WA_INTERNO_NUM`, `SCBA_USUARIO`, `SCBA_PASSWORD`, `PORT`. No hay `.env.example` en el repo -> onboarding frágil.

**Malas prácticas de deployment**
- Misma carpeta de repo para “sitio estático” y “API” sin separación de artefactos ni CI.
- Endpoint de diagnóstico en producción (ver seguridad).

---

## 4. Performance

- **Carga inicial**: varios round-trips a CDNs (Supabase, MP, Fonts) + HTML grande monolítico.
- **Búsqueda**: muchas peticiones HTTP secuenciales dentro del scraper (SCBA por juzgado + PJN); tiempo de CPU/red dominado por el scraper, no por Flask.
- **API**: 1 worker Gunicorn -> una búsqueda pesada bloquea el worker para otras peticiones cortas.
- **Concurrencia**: varios `subprocess` + posible condición de carrera si en el futuro se reintroduce escritura compartida a `resultado.json`.

**Mejoras concretas**
- Cola de trabajos (Redis/RQ, Celery, o worker dedicado en Railway) + endpoint que solo encola y devuelve `job_id`; SSE o polling desde el worker.
- CDN para assets estáticos si el HTML se sirve desde Pages; `preconnect` a orígenes críticos.
- Cache HTTP corto solo para `GET /api/info` y `/health` si se exponen detrás de balanceador; **no** cachear búsquedas.
- Aumentar workers solo si el scraper se externaliza; con subprocess pesado, más workers = más RAM y riesgo de rate-limit SCBA/PJN.

---

## 5. Seguridad

| Hallazgo | Severidad |
|----------|-----------|
| `CORS(origins=["*"])` | Alto en escenario multi-sitio; aceptable solo si la API es estrictamente pública y sin cookies sensibles |
| Token JWT en **query string** (`/buscar/stream?token=`) | Alto (logs servidor, proxies, historial, referrers) |
| `verificar_token`: fallback `jwt.decode(..., verify_signature=False)` | **Crítico** para rutas que lo usen: acepta tokens forjados |
| `ADMIN_TOKEN` vacío: `/resolver-pjn` **no exige** autenticación | **Crítico** en prod si no configurás token |
| `GET /debug-scraper` sin auth, expone stderr/stdout del scraper | **Crítico** en prod |
| Webhook MP: no se ve verificación de firma `x-signature` | Alto (depende de MP y de secretos; revisar docs MP) |
| `SUPABASE_ANON_KEY` en el HTML | Esperable; la seguridad real es **RLS** en Supabase (no auditable desde este repo) |
| Login vía cliente con anon key + perfil/upsert desde cliente | Depende totalmente de RLS; cualquier fallo de políticas expone datos |

**Acciones**: CORS acotado a orígenes conocidos; token vía cookie httpOnly o endpoint que abre SSE con POST body (patrón más incómodo pero más seguro); eliminar decode JWT sin firma; proteger o borrar `/debug-scraper` en prod; forzar `ADMIN_TOKEN`; validar webhooks MP.

---

## 6. Escalabilidad

- **Más usuarios concurrentes**: el cuello de botella es el **subprocess + scraping** en el mismo proceso que sirve HTTP; 1 worker empeora el colapso.
- **Más endpoints**: el monolito Flask escala en líneas pero no en equipos; sin capas (servicios, repos) el costo cognitivo sube rápido.

**Límites claros**
- Un worker, tareas largas (hasta ~300 s), dependencia de sitios externos (SCBA login, PJN captcha), sin cola ni idempotencia visible para pagos/webhooks más allá del update simple.

---

## 7. Mejores prácticas y migración

Frente a **Vercel/Netlify + API** o **SSR**: hoy es un **monolito “BFF ligero” + SPA en un archivo**. No necesitás SSR salvo SEO fuerte en landing; el problema real es **separación limpia de API URL**, **seguridad** y **colas de trabajo**, no el framework del frontend.

**¿Migrar?**
- **Sí conviene** evolucionar hacia: repo o carpetas `frontend/` + `backend/`, build mínimo (Vite), variables de entorno inyectadas en build para `VITE_API_URL`, y worker para scrapers.
- **No hace falta** migrar a Next/Vercel “por moda” si el producto es interno/poco SEO; sí hace falta endurecer seguridad y desacoplar hosting (dominio único detrás de un reverse proxy o API subdomain documentada).

---

## 8. Diagnóstico final

**🟢 Qué está bien**
- Separación conceptual scraper ↔ API.
- Secretos SCBA solo por entorno en `buscar_simple.py`.
- SSE con heartbeat y timeout acotado en streaming.
- `entrypoint_gunicorn.py` y comentarios de Railway muestran cuidado por el `PORT`.
- `.gitignore` excluye `.env` y `resultado.json`.

**🟡 Qué es mejorable**
- Monolito HTML gigante, encoding roto en varios strings UI (“ContraseÃ±a”, “MÃS POPULAR”).
- Duplicación de lógica de planes/precios entre HTML y `PLANES` en Python.
- `nixpacks` redundante; README vacío; sin CI.
- Endpoint `/consultas` en backend mientras el front usa Supabase directo.

**🔴 Qué es un problema real**
- `verificar_token` con JWT sin verificación de firma.
- `/resolver-pjn` sin admin si `ADMIN_TOKEN` vacío.
- `/debug-scraper` expuesto.
- Token en query para SSE.
- Riesgo de **origen API** si Pages y Railway no comparten host.
- `/buscar` con `nombre.split()` vs scraper; `resultado.json` no alineado con el scraper actual.
- Bug JS en `guardarCuenta`: usa `alertEl` no definido (y `alert` como nombre de variable confunde con `window.alert`).

---

## 9. Recomendaciones accionables (prioridad de mayor impacto a menor)

1. **Arreglar auth en backend**: quitar el fallback `pyjwt.decode(..., verify_signature=False)`; validar sesión solo con Supabase (o JWKS con firma).
2. **Proteger operaciones sensibles**: exigir `ADMIN_TOKEN` no vacío para `/resolver-pjn`; eliminar o restringir `/debug-scraper` (IP allowlist, env `DEBUG=1` solo en staging).
3. **Definir un solo modelo de deploy**: mismo dominio -> Railway (o proxy) **o** `API_URL` explícita en frontend + CORS con lista blanca + subdominio `api.` para webhooks MP coherentes con `APP_URL`.
4. **SSE y token**: diseño alternativo (cookie sesión server-side, o ticket de un solo uso generado con POST que el EventSource consume sin bearer en URL).
5. **Webhook MercadoPago**: implementar verificación de firma según documentación actual de MP.
6. **Corregir `/buscar`**: pasar nombre como un solo argumento (como en stream) y DNI opcional; o deprecar `/buscar` si solo usás stream.
7. **Unificar datos del panel**: o todo vía API con service role y políticas claras, o todo vía Supabase con RLS revisada por un checklist; evitar mezcla accidental.
8. **Cola de trabajos** para búsquedas + límite de tasa por usuario/IP para escalar sin tumbar el worker.
9. **Frontend**: corregir encoding UTF-8 del archivo; arreglar `guardarCuenta`; extraer JS/CSS y añadir build mínimo + lint.
10. **Documentación**: `.env.example` y diagrama “DNS -> Railway vs Pages” + checklist de variables Railway.

