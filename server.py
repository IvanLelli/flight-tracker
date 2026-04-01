#!/usr/bin/env python3
"""
Flight Tracker Server
Riceve dati GPS dall'app Flutter e li trasmette live via WebSocket.

Installazione:
    pip install flask flask-socketio flask-cors eventlet

Avvio:
    python server.py

Oppure con gunicorn (produzione):
    gunicorn --worker-class eventlet -w 1 server:app --bind 0.0.0.0:5000
"""

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

DB_PATH = os.path.join(os.path.dirname(__file__), 'flights.db')

# -------------------------------------------------------
# Database
# -------------------------------------------------------

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(error):
    db = g.pop('db', None)
    if db:
        db.close()

def init_db():
    """Crea le tabelle al primo avvio."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS flight_points (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                flight_id   TEXT    NOT NULL,
                lat         REAL    NOT NULL,
                lon         REAL    NOT NULL,
                altitude    REAL    DEFAULT 0,
                speed_kmh   REAL    DEFAULT 0,
                bearing     REAL    DEFAULT 0,
                accuracy    REAL    DEFAULT 0,
                timestamp   TEXT    NOT NULL,
                received_at TEXT    NOT NULL
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_flight_id ON flight_points(flight_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_timestamp  ON flight_points(timestamp)')
        conn.commit()
    print(f"[DB] Database pronto: {DB_PATH}")


# -------------------------------------------------------
# Serve la dashboard (file statico)
# -------------------------------------------------------

@app.route('/')
def index():
    return '''<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Flight Tracker</title></head>
<body style="font-family:sans-serif;padding:40px;background:#0f1117;color:#e2e8f0">
<h1>&#9992; Flight Tracker Server</h1>
<p style="color:#22c55e;font-size:20px">&#9679; Server attivo e funzionante!</p>
<p>Usa la dashboard sul tuo PC o hosting per vedere la mappa.</p>
</body></html>'''


# -------------------------------------------------------
# API: ricezione dati dall'app
# -------------------------------------------------------

@app.route('/api/data', methods=['POST'])
def receive_data():
    """Riceve un punto GPS dall'app Flutter."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'JSON non valido'}), 400

    required = ['lat', 'lon', 'flight_id', 'timestamp']
    if not all(k in data for k in required):
        return jsonify({'error': f'Campi mancanti: {required}'}), 400

    point = {
        'flight_id':   str(data['flight_id']),
        'lat':         float(data.get('lat', 0)),
        'lon':         float(data.get('lon', 0)),
        'altitude':    float(data.get('altitude', 0)),
        'speed_kmh':   float(data.get('speed_kmh', 0)),
        'bearing':     float(data.get('bearing', 0)),
        'accuracy':    float(data.get('accuracy', 0)),
        'timestamp':   str(data.get('timestamp', '')),
        'received_at': datetime.utcnow().isoformat(),
    }

    # Salva nel database
    db = get_db()
    db.execute('''
        INSERT INTO flight_points
        (flight_id, lat, lon, altitude, speed_kmh, bearing, accuracy, timestamp, received_at)
        VALUES (:flight_id, :lat, :lon, :altitude, :speed_kmh, :bearing, :accuracy, :timestamp, :received_at)
    ''', point)
    db.commit()

    # Push in tempo reale a tutti i browser connessi via WebSocket
    socketio.emit('new_point', point)

    print(f"[{point['received_at'][:19]}] {point['flight_id']} | "
          f"lat={point['lat']:.5f} lon={point['lon']:.5f} "
          f"alt={point['altitude']:.0f}m spd={point['speed_kmh']:.1f}km/h")

    return jsonify({'ok': True, 'received_at': point['received_at']})


# -------------------------------------------------------
# API: lista voli registrati
# -------------------------------------------------------

@app.route('/api/flights', methods=['GET'])
def list_flights():
    db = get_db()
    rows = db.execute('''
        SELECT flight_id,
               COUNT(*)       AS points,
               MIN(timestamp) AS started,
               MAX(timestamp) AS ended,
               MAX(altitude)  AS max_alt,
               MAX(speed_kmh) AS max_speed
        FROM flight_points
        GROUP BY flight_id
        ORDER BY started DESC
        LIMIT 50
    ''').fetchall()
    return jsonify([dict(r) for r in rows])


# -------------------------------------------------------
# API: tutti i punti di un volo
# -------------------------------------------------------

@app.route('/api/flights/<flight_id>', methods=['GET'])
def get_flight(flight_id):
    db = get_db()
    rows = db.execute('''
        SELECT lat, lon, altitude, speed_kmh, bearing, accuracy, timestamp
        FROM flight_points
        WHERE flight_id = ?
        ORDER BY timestamp ASC
    ''', (flight_id,)).fetchall()
    if not rows:
        return jsonify({'error': 'Volo non trovato'}), 404
    return jsonify([dict(r) for r in rows])


# -------------------------------------------------------
# API: ultimo punto (polling di fallback)
# -------------------------------------------------------

@app.route('/api/flights/<flight_id>/latest', methods=['GET'])
def get_latest(flight_id):
    db = get_db()
    row = db.execute('''
        SELECT * FROM flight_points
        WHERE flight_id = ?
        ORDER BY timestamp DESC LIMIT 1
    ''', (flight_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Nessun dato'}), 404
    return jsonify(dict(row))


# -------------------------------------------------------
# WebSocket events
# -------------------------------------------------------

@socketio.on('connect')
def on_connect():
    print("[WS] Browser connesso")

@socketio.on('disconnect')
def on_disconnect():
    print("[WS] Browser disconnesso")


# -------------------------------------------------------
# Avvio
# -------------------------------------------------------

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    print(f"[SERVER] In ascolto su http://0.0.0.0:{port}")
    print(f"[SERVER] Dashboard: http://0.0.0.0:{port}/")
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
