"""
CONTRATA SEGURO - API Backend v2.2
Flask + Supabase + MercadoPago + SSE streaming + Flujo PJN pendiente
"""
import os
import logging
import subprocess, json, re, sys, threading, queue, time, hmac, hashlib, tempfile, uuid
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
load_dotenv(Path(_PROJECT_ROOT) / ".env")


def _env_truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


_DEBUG_LOCAL = _env_truthy("DEBUG_LOCAL")

from flask import Flask, request, jsonify, Response, stream_with_context, send_file, g
from flask_cors import CORS

import mercadopago
import requests
from supabase import create_client

from pjn_session import export_cookies_path, get_results as pjn_get_results, prepare as pjn_prepare, touch as pjn_touch, verify as pjn_verify

_STATIC_DIR = os.path.join(_PROJECT_ROOT, "static")
app = Flask(__name__)
CORS(app, origins=["*"])

if _DEBUG_LOCAL:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [local] %(message)s",
        force=True,
    )
    app.logger.setLevel(logging.INFO)
    app.logger.info(
        "DEBUG_LOCAL activo: se registran método, ruta, status y tiempo (no uses en prod con datos sensibles)."
    )


@app.before_request
def _debug_before_request():
    if not _DEBUG_LOCAL:
        return
    g._debug_t0 = time.time()


@app.after_request
def _debug_after_request(resp):
    if not _DEBUG_LOCAL:
        return resp
    t0 = getattr(g, "_debug_t0", None)
    dt_ms = (time.time() - t0) * 1000.0 if t0 is not None else -1.0
    app.logger.info("%s %s -> %s (%.1f ms)", request.method, request.path, resp.status_code, dt_ms)
    return resp

API_SERVICIO = "Contrata Seguro API"
API_VERSION = "2.2"

SUPABASE_URL    = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY    = os.environ.get("SUPABASE_SERVICE_KEY", "")
# Credenciales MP: el Access Token (API) y la clave de firma de webhooks son distintos.
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN", "")  # Credenciales → Access token (crear preferencia, GET payment)
MP_WEBHOOK_SECRET = os.environ.get("MP_WEBHOOK_SECRET", "").strip()  # Tus integraciones → Webhooks → clave secreta
APP_URL         = os.environ.get("APP_URL", "https://contrataseguro.ar")
WA_INTERNO_NUM  = os.environ.get("WA_INTERNO_NUM", "5491135688283")
ADMIN_TOKEN     = os.environ.get("ADMIN_TOKEN", "")
# Opcional: Cloudflare Turnstile (humano) antes de /pjn/prepare — https://developers.cloudflare.com/turnstile/
TURNSTILE_SITE_KEY = (os.environ.get("TURNSTILE_SITE_KEY") or "").strip()
TURNSTILE_SECRET_KEY = (os.environ.get("TURNSTILE_SECRET_KEY") or "").strip()

supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL else None
mp_sdk   = mercadopago.SDK(MP_ACCESS_TOKEN) if MP_ACCESS_TOKEN else None

PLANES = {
    "basico":      {"precio": 15000, "creditos": 20,   "titulo": "Plan Basico"},
    "profesional": {"precio": 45000, "creditos": 9999, "titulo": "Plan Profesional"},
    "credito_5":   {"precio": 2500,  "creditos": 1,    "titulo": "Por consulta"},
}

# Mercado Pago: statement_descriptor max 13 caracteres (doc oficial).
_MP_STATEMENT_DESCRIPTOR = "CONTRATA SEGU"
_MP_WEBHOOK_UNSIGNED_LOGGED = False


def _mp_evento_es_pago(payload):
    ev = (
        payload.get("type")
        or payload.get("action")
        or request.args.get("topic", "")
        or ""
    ).lower()
    return "payment" in ev


def _mp_extraer_payment_id(payload):
    pid = payload.get("data", {}).get("id")
    if pid is None:
        pid = request.args.get("data.id") or request.args.get("id")
    return str(pid) if pid not in (None, "") else ""


def _mp_manifest_data_id_for_signature(data_id_raw):
    s = str(data_id_raw).strip()
    if re.fullmatch(r"[a-zA-Z0-9]+", s):
        return s.lower()
    return s


def _mp_manifest_string(data_id, x_request_id, ts):
    parts = []
    if data_id is not None and str(data_id).strip() != "":
        parts.append(f"id:{_mp_manifest_data_id_for_signature(data_id)};")
    if x_request_id:
        parts.append(f"request-id:{x_request_id};")
    if ts:
        parts.append(f"ts:{ts};")
    return "".join(parts)


def _mp_parse_x_signature_header(header_val):
    if not header_val:
        return None, None
    ts, v1 = None, None
    for part in header_val.split(","):
        part = part.strip()
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        if k == "ts":
            ts = v
        elif k == "v1":
            v1 = v
    return ts, v1


def _mp_verificar_firma_webhook(payment_id_for_manifest):
    global _MP_WEBHOOK_UNSIGNED_LOGGED
    if not MP_WEBHOOK_SECRET:
        if not _MP_WEBHOOK_UNSIGNED_LOGGED:
            print("[MP] MP_WEBHOOK_SECRET no definida: se aceptan webhooks sin validar firma", flush=True)
            _MP_WEBHOOK_UNSIGNED_LOGGED = True
        return True
    x_sig = request.headers.get("x-signature") or request.headers.get("X-Signature")
    if not x_sig:
        return False
    ts, v1 = _mp_parse_x_signature_header(x_sig)
    if not ts or not v1:
        return False
    data_id = request.args.get("data.id") or payment_id_for_manifest or ""
    x_rid = request.headers.get("x-request-id") or request.headers.get("X-Request-Id") or ""
    manifest = _mp_manifest_string(data_id, x_rid, ts)
    expected = hmac.new(
        MP_WEBHOOK_SECRET.encode("utf-8"),
        manifest.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, v1)


def _mp_extraer_preference_id(pay_data):
    pid = pay_data.get("preference_id")
    if pid is None and isinstance(pay_data.get("preference"), dict):
        pid = pay_data["preference"].get("id")
    s = str(pid or "").strip()
    return s or None


def _mp_claim_pago_idempotente(usuario_id, plan_key, plan, preference_id, payment_id, monto):
    """Insert único por mp_payment_id antes de acreditar; evita doble crédito con webhooks duplicados."""
    if not supabase:
        return "ok"
    row = {
        "usuario_id": usuario_id,
        "mp_preference_id": preference_id,
        "mp_payment_id": str(payment_id),
        "tipo": plan_key,
        "monto": monto,
        "creditos_agregados": plan["creditos"],
        "estado": "aprobado",
    }
    try:
        supabase.table("pagos").insert(row).execute()
    except Exception as e:
        err = str(e).lower()
        if "duplicate" in err or "unique" in err or "23505" in err:
            return "duplicate"
        print(f"[MP] claim insert pagos: {e}", flush=True)
        return "error"
    if preference_id:
        try:
            supabase.table("pagos").delete().eq("mp_preference_id", preference_id).eq("estado", "pendiente").execute()
        except Exception as e:
            print(f"[MP] limpiar fila pendiente: {e}", flush=True)
    return "ok"

def get_perfil(user_id):
    if not supabase: return None
    try:
        r = supabase.table("perfiles").select("*").eq("id", user_id).single().execute()
        return r.data
    except: return None

def verificar_token(req):
    auth = req.headers.get("Authorization", "")
    if not auth.startswith("Bearer "): return None, "Sin token"
    token = auth[7:]
    if not supabase: return type('obj',(object,),{'id':'demo'})(), None
    try:
        user = supabase.auth.get_user(token)
        if user and user.user: return user.user, None
        return None, "Token invalido"
    except Exception as e:
        return None, str(e)

def enviar_alerta_wa_interno(nombre, usuario_email, consulta_id):
    """Alerta al operador para resolver PJN manualmente"""
    msg = (
        f"*Contrata Seguro - Consulta PJN pendiente*\n\n"
        f"Nombre: {nombre}\n"
        f"Usuario: {usuario_email}\n"
        f"ID: {consulta_id}\n"
        f"Accion requerida: consultar PJN manualmente"
    )
    print(f"[ALERTA_WA] {WA_INTERNO_NUM}: {msg}", flush=True)
    # TODO: integrar API real de WhatsApp (Twilio / CallMeBot / 360dialog)

def guardar_consulta(usuario_id, nombre, resultado, usar_credito, estado_pjn="ok"):
    if not supabase or not usuario_id: return None
    try:
        if usar_credito:
            pa = get_perfil(usuario_id)
            if pa:
                supabase.table("perfiles").update({
                    "creditos":        max(0, pa.get("creditos",0)-1),
                    "creditos_usados": pa.get("creditos_usados",0)+1,
                }).eq("id", usuario_id).execute()
        insert_data = {
            "usuario_id":    usuario_id,
            "nombre_buscado": nombre.upper(),
            "total_causas":  resultado.get("total", 0),
            "causas_scba":   resultado.get("causas_scba", 0),
            "causas_pjn":    resultado.get("causas_pjn", 0),
            "resultado":     resultado,
        }
        try: insert_data["estado_pjn"] = estado_pjn
        except: pass
        r = supabase.table("consultas").insert(insert_data).execute()
        return r.data[0]["id"] if r.data else None
    except Exception as e:
        print(f"Error guardando consulta: {e}", flush=True)
        return None

def _api_scraper_trace(msg):
    """Visible en logs del contenedor (no en el JSON de acceso HTTP del edge)."""
    print(f"[API/buscar_stream] {msg}", flush=True, file=sys.stderr)


def _argv_buscar_simple(nombre, cuil=None, caratula="apellido", pjn_cookies_file=None, skip_pjn=False):
    """CLI de buscar_simple: --cuil o nombre con --caratula apellido|completo."""
    script = os.path.join(_PROJECT_ROOT, "buscar_simple.py")
    caratula = (caratula or "apellido").strip().lower()
    if caratula not in ("apellido", "completo"):
        caratula = "apellido"
    base = [sys.executable, script, "--caratula", caratula]
    if skip_pjn:
        base += ["--skip-pjn"]
    if pjn_cookies_file:
        base += ["--pjn-cookies-file", pjn_cookies_file]
    if cuil:
        dig = re.sub(r"\D", "", str(cuil))
        return base + ["--cuil", dig]
    return base + [nombre.strip()]


def correr_scraper_stream(nombre, q, cuil=None, caratula="apellido", pjn_session_id=None,
                          scba_usuario=None, scba_password=None, pjn_usuario=None, pjn_password=None):
    pjn_cookies_path = None
    try:
        if pjn_session_id:
            pjn_cookies_path = os.path.join(
                tempfile.gettempdir(),
                f"pjn_cookies_{os.getpid()}_{uuid.uuid4().hex}.json",
            )
            if not export_cookies_path(pjn_session_id, pjn_cookies_path):
                _api_scraper_trace(
                    f"pjn_session_id inválido o expirado: {pjn_session_id!r}",
                )
                pjn_cookies_path = None
            else:
                pjn_touch(pjn_session_id)
        # Si la sesión ya tiene resultados PJN pre-computados (captcha resuelto + búsqueda hecha),
        # se le indica al subprocess que omita PJN para evitar un segundo captcha.
        pjn_stored = pjn_get_results(pjn_session_id) if pjn_session_id else None
        skip_pjn = pjn_stored is not None
        _api_scraper_trace(
            f"lanzando scraper nombre={nombre!r} cuil={cuil!r} caratula={caratula!r} "
            f"pjn_session={'ok' if pjn_cookies_path else 'no'} skip_pjn={skip_pjn}",
        )
        argv = _argv_buscar_simple(
            nombre,
            cuil=cuil,
            caratula=caratula,
            pjn_cookies_file=pjn_cookies_path if not skip_pjn else None,
            skip_pjn=skip_pjn,
        )
        # stderr del hijo al stderr del proceso Flask/Gunicorn: logs en tiempo real (Railway, local).
        env_sub = dict(os.environ)
        # Siempre pisa las credenciales con las del usuario (aunque estén vacías) para que
        # las variables globales de Railway no se filtren a búsquedas de otros usuarios.
        env_sub["SCBA_USUARIO"] = scba_usuario or ""
        env_sub["SCBA_PASSWORD"] = scba_password or ""
        env_sub["PJN_USUARIO"]  = pjn_usuario or ""
        env_sub["PJN_PASSWORD"] = pjn_password or ""
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
            text=True, bufsize=1,
            cwd=_PROJECT_ROOT,
            env=env_sub,
        )
        resultado_final = None
        n_progreso = 0
        n_log = 0
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("PROGRESO:"):
                try:
                    q.put(("progreso", json.loads(line[9:])))
                    n_progreso += 1
                except Exception:
                    pass
            elif line.startswith("LOG:"):
                try:
                    q.put(("log", json.loads(line[4:])))
                    n_log += 1
                except Exception:
                    pass
            elif line.startswith("RESULTADO:"):
                try: resultado_final = json.loads(line[10:])
                except: pass
        proc.wait(timeout=10)
        _api_scraper_trace(
            f"scraper terminó returncode={proc.returncode} líneas_progreso={n_progreso} líneas_log={n_log} "
            f"resultado={'ok' if resultado_final else 'falta'}",
        )
        if not resultado_final:
            json_path = os.path.join(_PROJECT_ROOT, "resultado.json")
            if os.path.exists(json_path):
                with open(json_path, encoding="utf-8") as f:
                    resultado_final = json.load(f)
        if resultado_final:
            q.put(("resultado", resultado_final))
        else:
            # stderr del scraper va a sys.stderr (no PIPE); revisar logs del servidor.
            q.put(("error", "Sin resultado (revisar logs del servidor / buscar_simple)"))
    except subprocess.TimeoutExpired: q.put(("error", "Timeout"))
    except Exception as e: q.put(("error", str(e)))
    finally:
        if pjn_cookies_path and os.path.isfile(pjn_cookies_path):
            try:
                os.remove(pjn_cookies_path)
            except OSError:
                pass
        q.put(("done", None))

@app.route("/", methods=["GET"])
def index():
    """Sitio en Railway: HTML en static/; metadatos JSON en /api/info."""
    resp = send_file(
        os.path.join(_STATIC_DIR, "index.html"),
        mimetype="text/html; charset=utf-8",
    )
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


def _turnstile_verify(token: str, remote_ip: str) -> bool:
    if not TURNSTILE_SECRET_KEY:
        return True
    if not (token or "").strip():
        return False
    try:
        r = requests.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={
                "secret": TURNSTILE_SECRET_KEY,
                "response": token.strip(),
                "remoteip": (remote_ip or "").strip(),
            },
            timeout=12,
        )
        data = r.json()
        return bool(data.get("success"))
    except Exception:
        return False


@app.route("/api/info", methods=["GET"])
def api_info():
    out = {"servicio": API_SERVICIO, "version": API_VERSION}
    if TURNSTILE_SITE_KEY:
        out["turnstile_site_key"] = TURNSTILE_SITE_KEY
    if _DEBUG_LOCAL:
        out["debug_local"] = True
    return jsonify(out)

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "servicio": API_SERVICIO, "version": API_VERSION})


_PJN_INIT_HDR = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "es-AR,es;q=0.9",
    "Referer": "https://scw.pjn.gov.ar/scw/home.seam",
    "Origin": "https://scw.pjn.gov.ar",
}

_PJN_WIDGET_FILENAME = re.compile(r"^widget\.scw\.[a-zA-Z0-9_.-]+\.html$")


def _rewrite_pjn_init_js_widget_urls(body: str) -> str:
    """
    PJN solo entrega el HTML completo del widget si el Referer es scw.pjn.gov.ar.
    El navegador desde otro origen recibe ~247 B ('Captcha no disponible').
    Reescribimos la URL del widget hacia nuestro proxy same-origin.
    """
    return re.sub(
        r"https://captcha\.pjn\.gov\.ar/api/(widget\.scw\.[^\"'\s>]+)",
        r"/pjn/captcha-widget/\1",
        body or "",
    )


@app.route("/pjn/captcha-init.js", methods=["GET"])
def pjn_captcha_init_js():
    """
    Sirve init.js del captcha PJN; reescribe la URL del HTML del widget para /pjn/captcha-widget/.
    El fetch a init.js lo hace el servidor con Referer SCW; el HTML del widget lo pide el
    navegador al proxy y el servidor vuelve a pedirlo a PJN con las mismas cabeceras.
    """
    try:
        r = requests.get(
            "https://captcha.pjn.gov.ar/api/init.js?sitekey=SCW",
            headers=_PJN_INIT_HDR,
            timeout=25,
        )
        r.raise_for_status()
        body = _rewrite_pjn_init_js_widget_urls(r.text or "")
        return Response(
            body,
            mimetype="application/javascript; charset=utf-8",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
            },
        )
    except Exception as e:
        return Response(
            "/* pjn captcha-init error: " + str(e).replace("*/", "* /") + " */\n",
            mimetype="application/javascript; charset=utf-8",
            status=502,
        )


@app.route("/pjn/captcha-widget/<path:fname>", methods=["GET"])
def pjn_captcha_widget_proxy(fname: str):
    """HTML del widget PJN vía servidor (Referer SCW); el cliente no puede cumplir esa condición."""
    if not _PJN_WIDGET_FILENAME.match(fname or ""):
        return Response("bad widget path", mimetype="text/plain; charset=utf-8", status=400)
    try:
        q = request.query_string.decode("utf-8") if request.query_string else ""
        url = "https://captcha.pjn.gov.ar/api/" + fname
        if q:
            url = url + "?" + q
        r = requests.get(
            url,
            headers={
                **_PJN_INIT_HDR,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            timeout=25,
        )
        r.raise_for_status()
        html = r.text or ""
        if len(html) < 3000:
            return Response(
                "<!-- pjn widget stub o error: respuesta corta desde PJN -->\n" + html,
                mimetype="text/html; charset=utf-8",
                status=502,
            )
        if re.search(r"(?i)<head[^>]*>", html):
            html = re.sub(
                r"(?i)<head([^>]*)>",
                r'<head\1><base href="https://captcha.pjn.gov.ar/api/">',
                html,
                count=1,
            )
        return Response(
            html,
            mimetype="text/html; charset=utf-8",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
        )
    except Exception as e:
        return Response(
            "<!DOCTYPE html><html><body>proxy error: "
            + str(e).replace("<", " ")
            + "</body></html>",
            mimetype="text/html; charset=utf-8",
            status=502,
        )


@app.route("/pjn/captcha-embed.html", methods=["GET"])
def pjn_captcha_embed_html():
    """
    Página mínima same-origin para el iframe del captcha (script /pjn/captcha-init.js).
    El HTML del widget se sirve vía /pjn/captcha-widget/ para evitar el filtro Referer de PJN.
    """
    resp = send_file(
        os.path.join(_STATIC_DIR, "pjn_captcha_embed.html"),
        mimetype="text/html; charset=utf-8",
    )
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/pjn/prepare", methods=["POST"])
def pjn_prepare_route():
    """Abre sesión SCW PJN y, si aplica, deja listo el widget para resolverlo en el front."""
    data = request.get_json(silent=True) or {}
    nombre = (data.get("nombre") or "").strip()
    cuil = (data.get("cuil") or "").strip()
    if not nombre and cuil:
        try:
            from cuitonline_lookup import lookup_cuitonline

            dig = re.sub(r"\D", "", cuil)
            if len(dig) == 11:
                co = lookup_cuitonline(dig)
                if co.ok:
                    nombre = (co.nombre or "").strip().upper()
        except Exception:
            pass
    if len(nombre) < 2:
        return jsonify({"ok": False, "error": "nombre_requerido"}), 400
    if TURNSTILE_SECRET_KEY:
        tf = (data.get("turnstile_token") or data.get("cf-turnstile-response") or "").strip()
        if not _turnstile_verify(tf, request.remote_addr or ""):
            return jsonify({"ok": False, "error": "turnstile_invalido"}), 403
    out = pjn_prepare(nombre)
    if _DEBUG_LOCAL:
        app.logger.info(
            "pjn/prepare ok=%s needs_widget=%s nombre_len=%s",
            out.get("ok"),
            out.get("needs_widget"),
            len(nombre),
        )
    if not out.get("ok"):
        return jsonify(out), 400
    return jsonify(out)


@app.route("/pjn/verify", methods=["POST"])
def pjn_verify_route():
    """Envía el token del widget (#captcha-response) con la misma sesión que /pjn/prepare."""
    data = request.get_json(silent=True) or {}
    sid = (data.get("session_id") or "").strip()
    token = (data.get("captcha_response") or data.get("token") or "").strip()
    if _DEBUG_LOCAL:
        app.logger.info("pjn/verify session_id_len=%s token_len=%s", len(sid), len(token))
    if not sid:
        return jsonify({"ok": False, "error": "session_id_requerido"}), 400
    out = pjn_verify(sid, token)
    if _DEBUG_LOCAL:
        app.logger.info("pjn/verify result ok=%s error=%s", out.get("ok"), out.get("error"))
    if not out.get("ok"):
        return jsonify(out), 400
    return jsonify(out)

@app.route("/buscar/stream", methods=["GET"])
def buscar_stream():
    nombre = request.args.get("nombre", "").strip()
    cuil = request.args.get("cuil", "").strip() or None
    caratula = request.args.get("caratula", "apellido").strip().lower()
    token = request.args.get("token", "")
    pjn_session_id = (request.args.get("pjn_session_id") or "").strip() or None
    if cuil:
        dig = re.sub(r"\D", "", cuil)
        if len(dig) != 11:
            return jsonify({"error": "CUIL/CUIT invalido (debe tener 11 digitos)"}), 400
    elif not nombre or len(nombre) < 2:
        return jsonify({"error": "Nombre invalido"}), 400

    if _DEBUG_LOCAL:
        app.logger.info(
            "buscar/stream nombre_len=%s cuil=%s caratula=%s token_present=%s pjn_session=%s",
            len(nombre or ""),
            bool(cuil),
            caratula,
            bool((token or "").strip()),
            bool(pjn_session_id),
        )

    _pcred = {}
    usuario_id = None; usar_credito = False; usuario_email = ""
    if token and supabase:
        try:
            user = supabase.auth.get_user(token)
            usuario_id    = user.user.id
            usuario_email = user.user.email or ""
            perfil = get_perfil(usuario_id)
            _pcred = perfil or {}
            if perfil:
                plan       = perfil.get("plan","gratis")
                suscripcion= perfil.get("suscripcion_activa", False)
                creditos   = perfil.get("creditos", 0)
                if plan == "profesional" and suscripcion: usar_credito = False
                elif creditos <= 0:
                    def gen_err():
                        yield f"data: {json.dumps({'tipo':'error','msg':'Sin creditos'})}\n\n"
                    return Response(stream_with_context(gen_err()), mimetype="text/event-stream",
                                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})
                else: usar_credito = True
        except: pass

    print(
        f"[API/buscar_stream] petición nombre={nombre!r} cuil={cuil!r} caratula={caratula!r}",
        flush=True,
        file=sys.stderr,
    )

    q = queue.Queue()
    t = threading.Thread(
        target=correr_scraper_stream,
        args=(nombre or "", q),
        kwargs={
            "cuil": cuil, "caratula": caratula, "pjn_session_id": pjn_session_id,
            "scba_usuario": _pcred.get("scba_usuario") or None,
            "scba_password": _pcred.get("scba_password") or None,
            "pjn_usuario":  _pcred.get("pjn_usuario")  or None,
            "pjn_password": _pcred.get("pjn_password")  or None,
        },
        daemon=True,
    )
    t.start()

    def generate():
        resultado_para_guardar = None
        inicio = time.time()
        while True:
            if time.time() - inicio > 320:
                yield f"data: {json.dumps({'tipo':'error','msg':'Timeout'})}\n\n"; break
            try: tipo, data = q.get(timeout=2)
            except queue.Empty:
                yield ": heartbeat\n\n"; continue

            if tipo == "progreso":
                yield f"data: {json.dumps({'tipo':'progreso', **data})}\n\n"

            elif tipo == "log":
                # Bitácora del scraper (también en stdout del hijo como LOG:). Ver DevTools → Consola.
                yield f"data: {json.dumps({'tipo':'log', **data})}\n\n"

            elif tipo == "resultado":
                # Mergear resultados PJN pre-computados (captcha resuelto) antes de emitir
                if pjn_session_id:
                    pjn_stored = pjn_get_results(pjn_session_id)
                    if pjn_stored:
                        pjn_causas, pjn_estado = pjn_stored
                        data = dict(data)
                        data["causas"] = (data.get("causas") or []) + pjn_causas
                        data["causas_pjn"] = len(pjn_causas)
                        data["total"] = len(data["causas"])
                        data["estado_pjn"] = pjn_estado
                resultado_para_guardar = data
                estado_pjn = data.get("estado_pjn", "ok")
                msg = {"tipo": "resultado", "resultado": data, "pjn_estado": estado_pjn}
                if estado_pjn == "captcha_required":
                    msg["pjn_mensaje"] = "Capital Federal en proceso. Te notificaremos cuando este lista."
                yield f"data: {json.dumps(msg)}\n\n"

            elif tipo == "error":
                yield f"data: {json.dumps({'tipo':'error','msg':str(data)})}\n\n"; break
            elif tipo == "done": break

        if resultado_para_guardar:
            estado_pjn  = resultado_para_guardar.get("estado_pjn", "ok")
            nom_guardar = (resultado_para_guardar.get("nombre") or nombre or "").strip().upper()
            if not nom_guardar and cuil:
                nom_guardar = f"CUIL {re.sub(r'\D', '', cuil)}"
            consulta_id = guardar_consulta(
                usuario_id, nom_guardar, resultado_para_guardar, usar_credito, estado_pjn
            )
            if estado_pjn == "captcha_required" and consulta_id:
                enviar_alerta_wa_interno(nom_guardar, usuario_email, consulta_id)
                yield f"data: {json.dumps({'tipo':'pjn_pendiente','consulta_id':consulta_id})}\n\n"

        yield f"data: {json.dumps({'tipo':'fin'})}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no","Connection":"keep-alive"})

@app.route("/buscar", methods=["GET","POST"])
def buscar():
    if request.method == "POST":
        data=request.get_json() or {}
        nombre=data.get("nombre","")
        cuil=(data.get("cuil") or "").strip() or None
        caratula=(data.get("caratula") or "apellido").strip().lower()
        token=data.get("token","")
    else:
        nombre=request.args.get("nombre","")
        cuil=request.args.get("cuil","").strip() or None
        caratula=request.args.get("caratula","apellido").strip().lower()
        token=request.args.get("token","")
    if cuil:
        dig=re.sub(r"\D","",cuil)
        if len(dig)!=11: return jsonify({"error":"CUIL/CUIT invalido"}),400
    elif not nombre or len(nombre.strip()) < 2: return jsonify({"error":"Nombre invalido"}),400
    _pcred2 = {}
    usuario_id=None; usar_credito=False; usuario_email=""
    if token and supabase:
        try:
            user=supabase.auth.get_user(token); usuario_id=user.user.id; usuario_email=user.user.email or ""
            perfil=get_perfil(usuario_id)
            _pcred2 = perfil or {}
            if perfil:
                if perfil.get("plan")=="profesional" and perfil.get("suscripcion_activa"): usar_credito=False
                elif perfil.get("creditos",0)<=0: return jsonify({"error":"Sin creditos"}),402
                else: usar_credito=True
        except: pass
    try:
        env_sub2 = dict(os.environ)
        env_sub2["SCBA_USUARIO"] = _pcred2.get("scba_usuario") or ""
        env_sub2["SCBA_PASSWORD"] = _pcred2.get("scba_password") or ""
        env_sub2["PJN_USUARIO"]  = _pcred2.get("pjn_usuario") or ""
        env_sub2["PJN_PASSWORD"] = _pcred2.get("pjn_password") or ""
        argv=_argv_buscar_simple(nombre,cuil=cuil,caratula=caratula)
        result=subprocess.run(argv,
                              capture_output=True,text=True,timeout=300,
                              cwd=_PROJECT_ROOT,env=env_sub2)
        json_path=os.path.join(_PROJECT_ROOT, "resultado.json")
        if os.path.exists(json_path):
            with open(json_path,encoding="utf-8") as f: resultado=json.load(f)
        else: return jsonify({"error":result.stderr[-500:] or "Sin resultado"}),500
    except subprocess.TimeoutExpired: return jsonify({"error":"Timeout"}),500
    except Exception as e: return jsonify({"error":str(e)}),500
    estado_pjn=resultado.get("estado_pjn","ok")
    nom_guardar=(resultado.get("nombre") or nombre or "").strip().upper()
    if not nom_guardar and cuil:
        nom_guardar=f"CUIL {re.sub(r'\D','',cuil)}"
    consulta_id=guardar_consulta(usuario_id,nom_guardar,resultado,usar_credito,estado_pjn)
    if estado_pjn=="captcha_required" and consulta_id:
        enviar_alerta_wa_interno(nom_guardar,usuario_email,consulta_id)
    return jsonify(resultado)

@app.route("/resolver-pjn", methods=["POST"])
def resolver_pjn():
    """Operador carga resultados manuales del PJN"""
    data = request.get_json() or {}
    if not ADMIN_TOKEN or data.get("token_admin") != ADMIN_TOKEN:
        return jsonify({"error":"No autorizado"}),403
    consulta_id = data.get("consulta_id")
    causas_pjn  = data.get("causas_pjn", [])
    if not consulta_id: return jsonify({"error":"consulta_id requerido"}),400
    if not supabase: return jsonify({"error":"Sin base de datos"}),500
    try:
        r = supabase.table("consultas").select("*").eq("id",consulta_id).single().execute()
        consulta = r.data
        if not consulta: return jsonify({"error":"Consulta no encontrada"}),404
        res = consulta.get("resultado",{})
        causas_nuevas = res.get("causas",[]) + causas_pjn
        res_upd = {**res,"causas":causas_nuevas,"total":len(causas_nuevas),"causas_pjn":len(causas_pjn),"estado_pjn":"ok"}
        supabase.table("consultas").update({
            "resultado":res_upd,"total_causas":len(causas_nuevas),
            "causas_pjn":len(causas_pjn),"estado_pjn":"ok"
        }).eq("id",consulta_id).execute()
        return jsonify({"ok":True,"consulta_id":consulta_id,"total":len(causas_nuevas)})
    except Exception as e: return jsonify({"error":str(e)}),500

@app.route("/consultas", methods=["GET"])
def mis_consultas():
    user,err=verificar_token(request)
    if err: return jsonify({"error":err}),401
    if not supabase: return jsonify({"consultas":[]})
    r=supabase.table("consultas").select("id,nombre_buscado,total_causas,causas_scba,causas_pjn,created_at,estado_pjn").eq("usuario_id",user.id).order("created_at",desc=True).limit(50).execute()
    return jsonify({"consultas":r.data or []})

_CAMPOS_PRIVADOS = {"scba_usuario", "scba_password", "pjn_usuario", "pjn_password"}

@app.route("/perfil", methods=["GET"])
def mi_perfil():
    user,err=verificar_token(request)
    if err: return jsonify({"error":err}),401
    p = get_perfil(user.id) or {}
    return jsonify({k: v for k, v in p.items() if k not in _CAMPOS_PRIVADOS})

@app.route("/perfil", methods=["PUT"])
def actualizar_perfil():
    user,err=verificar_token(request)
    if err: return jsonify({"error":err}),401
    data=request.get_json() or {}
    campos={k:v for k,v in data.items() if k in ["nombre","empresa","cuit"]}
    supabase.table("perfiles").update(campos).eq("id",user.id).execute()
    return jsonify({"ok":True})

@app.route("/perfil/credenciales", methods=["PUT"])
def guardar_credenciales():
    user, err = verificar_token(request)
    if err: return jsonify({"error": err}), 401
    data = request.get_json() or {}
    campos = {k: v for k, v in data.items()
              if k in ("scba_usuario", "scba_password", "pjn_usuario", "pjn_password") and v}
    if not campos:
        return jsonify({"error": "No se enviaron credenciales válidas"}), 400
    supabase.table("perfiles").update(campos).eq("id", user.id).execute()
    return jsonify({"ok": True})

@app.route("/perfil/credenciales-status", methods=["GET"])
def estado_credenciales():
    user, err = verificar_token(request)
    if err: return jsonify({"error": err}), 401
    p = get_perfil(user.id) or {}
    return jsonify({
        "scba_configuradas": bool(p.get("scba_usuario") and p.get("scba_password")),
        "pjn_configuradas":  bool(p.get("pjn_usuario")  and p.get("pjn_password")),
        "scba_usuario":  p.get("scba_usuario")  or "",
        "scba_password": p.get("scba_password") or "",
        "pjn_usuario":   p.get("pjn_usuario")   or "",
        "pjn_password":  p.get("pjn_password")  or "",
    })

@app.route("/pagar", methods=["POST"])
def crear_pago():
    user,err=verificar_token(request)
    if err: return jsonify({"error":err}),401
    data=request.get_json() or {}; plan_key=data.get("plan","")
    if plan_key not in PLANES: return jsonify({"error":"Plan invalido"}),400
    plan=PLANES[plan_key]; perfil=get_perfil(user.id)
    if not mp_sdk: return jsonify({"error":"MercadoPago no configurado"}),500
    payer_email = (perfil.get("email") if perfil else None) or getattr(user, "email", None) or ""
    pref_data={"items":[{"title":f"Contrata Seguro - {plan['titulo']}","quantity":1,"unit_price":float(plan["precio"]),"currency_id":"ARS"}],"payer":{"email":payer_email},"external_reference":f"{user.id}|{plan_key}","back_urls":{"success":f"{APP_URL}?pago=ok&plan={plan_key}","failure":f"{APP_URL}?pago=error","pending":f"{APP_URL}?pago=pendiente"},"auto_return":"approved","notification_url": f"{APP_URL.rstrip('/')}/webhook/mp","statement_descriptor":_MP_STATEMENT_DESCRIPTOR}
    result=mp_sdk.preference().create(pref_data)
    status=result.get("status")
    pref=result.get("response") or {}
    if status not in (200, 201) or not pref.get("id"):
        err_body=pref if isinstance(pref,dict) else {}
        msg=err_body.get("message") or err_body.get("error") or err_body.get("cause") or str(result)
        print(f"[MP] preferencia rechazada status={status} body={err_body}", flush=True)
        return jsonify({"error":f"MercadoPago: {msg}"}),502
    # Producción: init_point (checkout real). Sandbox: suele venir sandbox_init_point.
    checkout_url = pref.get("init_point") or pref.get("sandbox_init_point")
    if not checkout_url:
        print(f"[MP] preferencia sin URL de checkout: keys={list(pref.keys())}", flush=True)
        return jsonify({"error":"MercadoPago no devolvio URL de pago (init_point)"}),502
    if supabase:
        try:
            supabase.table("pagos").insert(
                {
                    "usuario_id": user.id,
                    "mp_preference_id": pref["id"],
                    "tipo": plan_key,
                    "monto": plan["precio"],
                    "creditos_agregados": plan["creditos"],
                    "estado": "pendiente",
                }
            ).execute()
        except Exception as e:
            print(f"[MP] insert pagos pendiente: {e}", flush=True)
    return jsonify({"preference_id":pref["id"],"init_point":checkout_url,"sandbox_init_point":pref.get("sandbox_init_point"),"init_point_prod":pref.get("init_point")})

@app.route("/webhook/mp", methods=["POST"])
def webhook_mp():
    data = request.get_json(silent=True) or {}
    if not _mp_evento_es_pago(data):
        return jsonify({"ok": True})
    payment_id = _mp_extraer_payment_id(data)
    if not payment_id or not mp_sdk:
        return jsonify({"ok": True})
    if not _mp_verificar_firma_webhook(payment_id):
        print("[MP] webhook rechazado: firma invalida o ausente", flush=True)
        return jsonify({"error": "invalid signature"}), 403
    try:
        payment = mp_sdk.payment().get(payment_id)
    except Exception as e:
        print(f"[MP] payment().get fallo: {e}", flush=True)
        return jsonify({"error": "retry"}), 500
    if payment.get("status") not in (200, 201):
        return jsonify({"ok": True})
    pay_data = payment.get("response") or {}
    estado = pay_data.get("status")
    ref = pay_data.get("external_reference", "")
    if not ref or "|" not in ref:
        return jsonify({"ok": True})
    usuario_id, plan_key = ref.split("|", 1)
    plan = PLANES.get(plan_key, {})
    if not plan or not supabase:
        return jsonify({"ok": True})
    if estado != "approved":
        return jsonify({"ok": True})
    esperado = float(plan["precio"])
    monto = pay_data.get("transaction_amount")
    try:
        monto_f = float(monto) if monto is not None else None
    except (TypeError, ValueError):
        monto_f = None
    if monto_f is None or abs(monto_f - esperado) > 0.02:
        print(f"[MP] monto no coincide plan={plan_key} esperado={esperado} recibido={monto!r}", flush=True)
        return jsonify({"ok": True})
    if (pay_data.get("currency_id") or "").upper() != "ARS":
        print(f"[MP] moneda inesperada: {pay_data.get('currency_id')!r}", flush=True)
        return jsonify({"ok": True})
    perfil = get_perfil(usuario_id)
    if not perfil:
        return jsonify({"ok": True})
    preference_id = _mp_extraer_preference_id(pay_data)
    claim = _mp_claim_pago_idempotente(usuario_id, plan_key, plan, preference_id, payment_id, esperado)
    if claim == "duplicate":
        return jsonify({"ok": True})
    if claim == "error":
        return jsonify({"error": "retry"}), 500
    from datetime import datetime, timedelta

    es_sus = plan_key in ("basico", "profesional")
    upd = {"creditos_usados": 0}
    if es_sus:
        upd.update(
            {
                "plan": plan_key,
                "suscripcion_activa": True,
                "suscripcion_vence": (datetime.utcnow() + timedelta(days=30)).isoformat(),
                "creditos": plan["creditos"],
            }
        )
    else:
        upd["creditos"] = perfil.get("creditos", 0) + plan["creditos"]
    try:
        supabase.table("perfiles").update(upd).eq("id", usuario_id).execute()
    except Exception as e:
        print(f"[MP] update perfil tras pago: {e}", flush=True)
        try:
            supabase.table("pagos").delete().eq("mp_payment_id", str(payment_id)).execute()
        except Exception as e2:
            print(f"[MP] rollback claim pagos: {e2}", flush=True)
        return jsonify({"error": "retry"}), 500
    return jsonify({"ok": True})

@app.route("/login", methods=["POST"])
def login_proxy():
    """Proxy de login a Supabase usando el cliente Python ya configurado"""
    data = request.get_json() or {}
    email = data.get("email","").strip()
    password = data.get("password","")
    if not email or not password:
        return jsonify({"error":"Email y contrasena requeridos"}), 400
    if not supabase:
        return jsonify({"error":"Servidor no configurado"}), 500
    try:
        # Usar el cliente Supabase Python que ya tiene la URL y keys configuradas
        result = supabase.auth.sign_in_with_password({"email": email, "password": password})
        if not result.session:
            return jsonify({"error":"Credenciales incorrectas"}), 401
        return jsonify({
            "access_token":  result.session.access_token,
            "refresh_token": result.session.refresh_token,
            "expires_in":    result.session.expires_in,
            "token_type":    "bearer",
            "user": {
                "id":    result.user.id,
                "email": result.user.email,
            }
        })
    except Exception as e:
        msg = str(e)
        if "Invalid login" in msg or "invalid_grant" in msg:
            return jsonify({"error":"Email o contrasena incorrectos"}), 401
        return jsonify({"error": msg}), 500


@app.route("/debug-scraper")
def debug_scraper():
    import subprocess, sys, os
    nombre = request.args.get("nombre", "MOSTEYRO")
    cuil = request.args.get("cuil", "").strip() or None
    caratula = request.args.get("caratula", "apellido")
    script = os.path.join(_PROJECT_ROOT, "buscar_simple.py")
    try:
        argv = _argv_buscar_simple(nombre, cuil=cuil, caratula=caratula)
        result = subprocess.run(
            argv,
            capture_output=True, text=True, timeout=30,
            cwd=_PROJECT_ROOT,
        )
        return jsonify({
            "stdout_last500": result.stdout[-500:],
            "stderr": result.stderr[-1000:],
            "returncode": result.returncode,
            "nombre": nombre.upper(),
            "argv": argv,
        })
    except Exception as e:
        return jsonify({"error": str(e)})


if __name__ == "__main__":
    port=int(os.environ.get("PORT",5000))
    app.run(host="0.0.0.0",port=port,threaded=True)
