"""
CONTRATA SEGURO - API Backend v2
Flask + Supabase + MercadoPago + Scraper SCBA/PJN con SSE streaming
"""
from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
import subprocess, json, os, sys, threading, queue, time
import mercadopago
from supabase import create_client, Client

app = Flask(__name__)
CORS(app, origins=["*"])

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN", "")
APP_URL = os.environ.get("APP_URL", "https://contrataseguro.ar")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL else None
mp_sdk   = mercadopago.SDK(MP_ACCESS_TOKEN) if MP_ACCESS_TOKEN else None

PLANES = {
    "basico":      {"precio": 15000, "creditos": 20,   "titulo": "Plan Basico"},
    "profesional": {"precio": 45000, "creditos": 9999, "titulo": "Plan Profesional"},
    "credito_5":   {"precio": 2500,  "creditos": 1,    "titulo": "Por consulta"},
}

def get_perfil(user_id):
    if not supabase: return None
    try:
        r = supabase.table("perfiles").select("*").eq("id", user_id).single().execute()
        return r.data
    except:
        return None

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
        try:
            import jwt as pyjwt
            payload = pyjwt.decode(token, options={"verify_signature": False})
            user_id = payload.get("sub")
            if user_id: return type('obj',(object,),{'id':user_id,'email':payload.get('email','')})(), None
        except: pass
        return None, str(e)

def correr_scraper_stream(nombre, q):
    try:
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "buscar_simple.py")
        proc = subprocess.Popen(
            [sys.executable, script] + nombre.upper().split(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        resultado_final = None
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("PROGRESO:"):
                try: q.put(("progreso", json.loads(line[9:])))
                except: pass
            elif line.startswith("RESULTADO:"):
                try: resultado_final = json.loads(line[10:])
                except: pass
        proc.wait(timeout=10)
        if not resultado_final:
            json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resultado.json")
            if os.path.exists(json_path):
                with open(json_path, encoding="utf-8") as f:
                    resultado_final = json.load(f)
        if resultado_final: q.put(("resultado", resultado_final))
        else:
            stderr = proc.stderr.read()[-500:] if proc.stderr else ""
            q.put(("error", stderr or "Sin resultado"))
    except subprocess.TimeoutExpired: q.put(("error", "Timeout"))
    except Exception as e: q.put(("error", str(e)))
    finally: q.put(("done", None))

@app.route("/", methods=["GET"])
def index():
    return jsonify({"servicio": "Contrata Seguro API v2", "version": "2.1"})

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

@app.route("/buscar/stream", methods=["GET"])
def buscar_stream():
    nombre = request.args.get("nombre", "").strip()
    token  = request.args.get("token", "")
    if not nombre or len(nombre) < 2:
        return jsonify({"error": "Nombre invalido"}), 400

    usuario_id = None; usar_credito = False
    if token and supabase:
        try:
            user = supabase.auth.get_user(token)
            usuario_id = user.user.id
            perfil = get_perfil(usuario_id)
            if perfil:
                plan = perfil.get("plan", "gratis")
                creditos = perfil.get("creditos", 0)
                suscripcion = perfil.get("suscripcion_activa", False)
                if plan == "profesional" and suscripcion: usar_credito = False
                elif creditos <= 0:
                    def gen_error():
                        yield f"data: {json.dumps({'tipo':'error','msg':'Sin creditos'})}\n\n"
                    return Response(stream_with_context(gen_error()), mimetype="text/event-stream",
                                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})
                else: usar_credito = True
        except: pass

    q = queue.Queue()
    t = threading.Thread(target=correr_scraper_stream, args=(nombre, q), daemon=True)
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
            elif tipo == "resultado":
                resultado_para_guardar = data
                yield f"data: {json.dumps({'tipo':'resultado','resultado':data})}\n\n"
            elif tipo == "error":
                yield f"data: {json.dumps({'tipo':'error','msg':str(data)})}\n\n"; break
            elif tipo == "done": break

        if resultado_para_guardar and usuario_id and supabase:
            try:
                if usar_credito:
                    pa = get_perfil(usuario_id)
                    if pa:
                        supabase.table("perfiles").update({
                            "creditos": max(0, pa.get("creditos",0)-1),
                            "creditos_usados": pa.get("creditos_usados",0)+1,
                        }).eq("id", usuario_id).execute()
                supabase.table("consultas").insert({
                    "usuario_id": usuario_id, "nombre_buscado": nombre.upper(),
                    "total_causas": resultado_para_guardar.get("total",0),
                    "causas_scba": resultado_para_guardar.get("causas_scba",0),
                    "causas_pjn": resultado_para_guardar.get("causas_pjn",0),
                    "resultado": resultado_para_guardar,
                }).execute()
            except Exception as e: print(f"Error guardando: {e}")
        yield f"data: {json.dumps({'tipo':'fin'})}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no","Connection":"keep-alive"})

@app.route("/buscar", methods=["GET", "POST"])
def buscar():
    if request.method == "POST":
        data = request.get_json() or {}; nombre = data.get("nombre",""); token = data.get("token","")
    else:
        nombre = request.args.get("nombre",""); token = request.args.get("token","")
    if not nombre or len(nombre.strip()) < 2: return jsonify({"error": "Nombre invalido"}), 400

    usuario_id = None; usar_credito = False
    if token and supabase:
        try:
            user = supabase.auth.get_user(token); usuario_id = user.user.id
            perfil = get_perfil(usuario_id)
            if perfil:
                if perfil.get("plan")=="profesional" and perfil.get("suscripcion_activa"): usar_credito = False
                elif perfil.get("creditos",0) <= 0: return jsonify({"error":"Sin creditos"}), 402
                else: usar_credito = True
        except: pass

    try:
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "buscar_simple.py")
        result = subprocess.run([sys.executable, script] + nombre.upper().split(),
                                capture_output=True, text=True, timeout=300,
                                cwd=os.path.dirname(os.path.abspath(__file__)))
        json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resultado.json")
        if os.path.exists(json_path):
            with open(json_path, encoding="utf-8") as f: resultado = json.load(f)
        else: return jsonify({"error": result.stderr[-500:] or "Sin resultado"}), 500
    except subprocess.TimeoutExpired: return jsonify({"error":"Timeout"}), 500
    except Exception as e: return jsonify({"error":str(e)}), 500

    if usuario_id and supabase and resultado:
        try:
            if usar_credito:
                pa = get_perfil(usuario_id)
                if pa: supabase.table("perfiles").update({"creditos":max(0,pa.get("creditos",0)-1),"creditos_usados":pa.get("creditos_usados",0)+1}).eq("id",usuario_id).execute()
            supabase.table("consultas").insert({"usuario_id":usuario_id,"nombre_buscado":nombre.upper(),"total_causas":resultado.get("total",0),"causas_scba":resultado.get("causas_scba",0),"causas_pjn":resultado.get("causas_pjn",0),"resultado":resultado}).execute()
        except Exception as e: print(f"Error: {e}")
    return jsonify(resultado)

@app.route("/consultas", methods=["GET"])
def mis_consultas():
    user, err = verificar_token(request)
    if err: return jsonify({"error":err}), 401
    if not supabase: return jsonify({"consultas":[]})
    r = supabase.table("consultas").select("id,nombre_buscado,total_causas,causas_scba,causas_pjn,created_at").eq("usuario_id",user.id).order("created_at",desc=True).limit(50).execute()
    return jsonify({"consultas": r.data or []})

@app.route("/perfil", methods=["GET"])
def mi_perfil():
    user, err = verificar_token(request)
    if err: return jsonify({"error":err}), 401
    return jsonify(get_perfil(user.id) or {})

@app.route("/perfil", methods=["PUT"])
def actualizar_perfil():
    user, err = verificar_token(request)
    if err: return jsonify({"error":err}), 401
    data = request.get_json() or {}
    campos = {k:v for k,v in data.items() if k in ["nombre","empresa","cuit"]}
    supabase.table("perfiles").update(campos).eq("id",user.id).execute()
    return jsonify({"ok":True})

@app.route("/pagar", methods=["POST"])
def crear_pago():
    user, err = verificar_token(request)
    if err: return jsonify({"error":err}), 401
    data = request.get_json() or {}; plan_key = data.get("plan","")
    if plan_key not in PLANES: return jsonify({"error":"Plan invalido"}), 400
    plan = PLANES[plan_key]; perfil = get_perfil(user.id)
    if not mp_sdk: return jsonify({"error":"MercadoPago no configurado"}), 500
    preference_data = {
        "items":[{"title":f"Contrata Seguro - {plan['titulo']}","quantity":1,"unit_price":float(plan["precio"]),"currency_id":"ARS"}],
        "payer":{"email":perfil.get("email","") if perfil else ""},
        "external_reference":f"{user.id}|{plan_key}",
        "back_urls":{"success":f"{APP_URL}?pago=ok&plan={plan_key}","failure":f"{APP_URL}?pago=error","pending":f"{APP_URL}?pago=pendiente"},
        "auto_return":"approved",
        "notification_url":"https://web-production-46da7.up.railway.app/webhook/mp",
        "statement_descriptor":"CONTRATA SEGURO",
    }
    result = mp_sdk.preference().create(preference_data); preference = result["response"]
    if supabase:
        try: supabase.table("pagos").insert({"usuario_id":user.id,"mp_preference_id":preference["id"],"tipo":plan_key,"monto":plan["precio"],"creditos_agregados":plan["creditos"],"estado":"pendiente"}).execute()
        except: pass
    return jsonify({"preference_id":preference["id"],"init_point":preference["init_point"]})

@app.route("/webhook/mp", methods=["POST"])
def webhook_mp():
    data = request.get_json() or {}
    topic = data.get("type") or request.args.get("topic","")
    payment_id = data.get("data",{}).get("id") or request.args.get("id")
    if topic != "payment" or not payment_id or not mp_sdk: return jsonify({"ok":True})
    payment = mp_sdk.payment().get(payment_id); pay_data = payment["response"]
    estado = pay_data.get("status"); ref = pay_data.get("external_reference","")
    if not ref or "|" not in ref: return jsonify({"ok":True})
    usuario_id, plan_key = ref.split("|",1); plan = PLANES.get(plan_key,{})
    if not plan or not supabase: return jsonify({"ok":True})
    if estado == "approved":
        perfil = get_perfil(usuario_id)
        if not perfil: return jsonify({"ok":True})
        from datetime import datetime, timedelta
        es_suscripcion = plan_key in ["basico","profesional"]
        update_data = {"creditos_usados":0}
        if es_suscripcion:
            update_data.update({"plan":plan_key,"suscripcion_activa":True,"suscripcion_vence":(datetime.utcnow()+timedelta(days=30)).isoformat(),"creditos":plan["creditos"]})
        else: update_data["creditos"] = perfil.get("creditos",0) + plan["creditos"]
        supabase.table("perfiles").update(update_data).eq("id",usuario_id).execute()
    return jsonify({"ok":True})

if __name__ == "__main__":
    port = int(os.environ.get("PORT",5000))
    app.run(host="0.0.0.0", port=port, threaded=True)
