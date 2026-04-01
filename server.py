#!/usr/bin/env python3
import sqlite3
import os
from datetime import datetime
from flask import Flask, request, jsonify, g
from flask_socketio import SocketIO
from flask_cors import CORS

app = Flask(__name__)
app.config['SECRET_KEY'] = 'cambia-questa-chiave-segreta-2024'
CORS(app, origins="*")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# Su Render il piano free non ha disco persistente — usiamo /tmp che è sempre scrivibile
DB_PATH = '/tmp/flights.db'

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(error):
    db = g.pop('db', None)
    if db: db.close()

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS flight_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flight_id TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL,
            altitude REAL DEFAULT 0, speed_kmh REAL DEFAULT 0,
            bearing REAL DEFAULT 0, accuracy REAL DEFAULT 0,
            timestamp TEXT NOT NULL, received_at TEXT NOT NULL)''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_flight_id ON flight_points(flight_id)')
        conn.commit()
    print(f"[DB] Database pronto: {DB_PATH}")

def save_and_emit(point):
    db = get_db()
    db.execute('''INSERT INTO flight_points
        (flight_id,lat,lon,altitude,speed_kmh,bearing,accuracy,timestamp,received_at)
        VALUES (:flight_id,:lat,:lon,:altitude,:speed_kmh,:bearing,:accuracy,:timestamp,:received_at)''', point)
    db.commit()
    socketio.emit('new_point', point)
    print(f"[{point['received_at'][:19]}] {point['flight_id']} lat={point['lat']:.5f} lon={point['lon']:.5f} alt={point['altitude']:.0f}m")

@app.route('/')
def index():
    return '''<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Flight Tracker</title></head>
<body style="font-family:sans-serif;padding:40px;background:#0f1117;color:#e2e8f0">
<h1>&#9992; Flight Tracker Server</h1>
<p style="color:#22c55e;font-size:20px">&#9679; Server attivo!</p>
<p style="color:#64748b">Endpoint disponibili:<br>
POST /api/data &nbsp;&nbsp;&nbsp;&nbsp;(app Flutter)<br>
GET &nbsp;/api/gpslogger &nbsp;(GPSLogger Android)<br>
GET &nbsp;/api/traccar &nbsp;&nbsp;(Traccar Client iPhone)<br>
GET &nbsp;/api/flights &nbsp;&nbsp;(lista voli)
</p>
</body></html>'''

@app.route('/api/data', methods=['POST'])
def receive_data():
    data = request.get_json(silent=True)
    if not data: return jsonify({'error': 'JSON non valido'}), 400
    point = {
        'flight_id': str(data.get('flight_id', 'app')),
        'lat': float(data.get('lat', 0)), 'lon': float(data.get('lon', 0)),
        'altitude': float(data.get('altitude', 0)), 'speed_kmh': float(data.get('speed_kmh', 0)),
        'bearing': float(data.get('bearing', 0)), 'accuracy': float(data.get('accuracy', 0)),
        'timestamp': str(data.get('timestamp', datetime.utcnow().isoformat())),
        'received_at': datetime.utcnow().isoformat(),
    }
    save_and_emit(point)
    return jsonify({'ok': True})

@app.route('/api/gpslogger', methods=['GET'])
def receive_gpslogger():
    try:
        point = {
            'flight_id': 'GPSLogger',
            'lat': float(request.args.get('lat', 0)), 'lon': float(request.args.get('lon', 0)),
            'altitude': float(request.args.get('alt', 0)),
            'speed_kmh': float(request.args.get('speed', 0)) * 3.6,
            'bearing': float(request.args.get('dir', 0)), 'accuracy': float(request.args.get('acc', 0)),
            'timestamp': datetime.utcnow().isoformat(), 'received_at': datetime.utcnow().isoformat(),
        }
        save_and_emit(point)
        return 'OK', 200
    except Exception as e:
        return str(e), 400

@app.route('/api/traccar', methods=['GET'])
def receive_traccar():
    try:
        point = {
            'flight_id': str(request.args.get('id', 'iPhone')),
            'lat': float(request.args.get('lat', 0)), 'lon': float(request.args.get('lon', 0)),
            'altitude': float(request.args.get('altitude', 0)),
            'speed_kmh': float(request.args.get('speed', 0)) * 1.852,
            'bearing': float(request.args.get('bearing', 0)), 'accuracy': float(request.args.get('accuracy', 0)),
            'timestamp': datetime.utcnow().isoformat(), 'received_at': datetime.utcnow().isoformat(),
        }
        save_and_emit(point)
        return 'OK', 200
    except Exception as e:
        return str(e), 400

@app.route('/api/flights', methods=['GET'])
def list_flights():
    db = get_db()
    rows = db.execute('''SELECT flight_id, COUNT(*) AS points, MIN(timestamp) AS started,
        MAX(timestamp) AS ended, MAX(altitude) AS max_alt, MAX(speed_kmh) AS max_speed
        FROM flight_points GROUP BY flight_id ORDER BY started DESC LIMIT 50''').fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/flights/<flight_id>', methods=['GET'])
def get_flight(flight_id):
    db = get_db()
    rows = db.execute('''SELECT lat,lon,altitude,speed_kmh,bearing,accuracy,timestamp
        FROM flight_points WHERE flight_id=? ORDER BY timestamp ASC''', (flight_id,)).fetchall()
    if not rows: return jsonify({'error': 'Volo non trovato'}), 404
    return jsonify([dict(r) for r in rows])

@app.route('/api/flights/<flight_id>/latest', methods=['GET'])
def get_latest(flight_id):
    db = get_db()
    row = db.execute('SELECT * FROM flight_points WHERE flight_id=? ORDER BY timestamp DESC LIMIT 1', (flight_id,)).fetchone()
    if not row: return jsonify({'error': 'Nessun dato'}), 404
    return jsonify(dict(row))

@socketio.on('connect')
def on_connect(): print("[WS] Browser connesso")

@socketio.on('disconnect')
def on_disconnect(): print("[WS] Browser disconnesso")

# Inizializza il DB all'avvio dell'app (necessario con gunicorn)
with app.app_context():
    init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
