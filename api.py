from flask import Flask, request, jsonify
from flask_cors import CORS
import subprocess, json, os, tempfile, sys

app = Flask(__name__)
CORS(app)

@app.route('/buscar', methods=['GET','POST'])
def buscar():
    # Obtener nombre del request
    if request.method == 'POST':
        data = request.get_json() or {}
        nombre = data.get('nombre','')
    else:
        nombre = request.args.get('nombre','')

    if not nombre or len(nombre.strip()) < 2:
        return jsonify({'error': 'Ingresá un nombre válido'}), 400

    # Correr el scraper
    try:
        result = subprocess.run(
            [sys.executable, 'buscar_simple.py'] + nombre.strip().upper().split(),
            capture_output=True, text=True, timeout=300,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        # Leer el JSON generado
        json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resultado.json')
        if os.path.exists(json_path):
            with open(json_path, encoding='utf-8') as f:
                datos = json.load(f)
            return jsonify(datos)
        else:
            return jsonify({'error': 'No se generó resultado', 'output': result.stdout[-500:]}), 500
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'La búsqueda tardó demasiado. Intentá de nuevo.'}), 504
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'servicio': 'Contrata Seguro API'})

@app.route('/', methods=['GET'])
def index():
    return jsonify({
        'servicio': 'Contrata Seguro API',
        'version': '1.0',
        'uso': 'GET /buscar?nombre=APELLIDO+NOMBRE',
        'ejemplo': '/buscar?nombre=MOSTEYRO+ANDREA'
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
