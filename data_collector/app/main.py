import os
import csv
import json
import time
import threading
import requests
from datetime import datetime
from flask import Flask, render_template, jsonify, request, send_file

app = Flask(__name__)

HA_TOKEN = os.environ.get('SUPERVISOR_TOKEN', '')
HA_URL = 'http://supervisor/core/api'
DATA_DIR = '/data'
CSV_FILE = os.path.join(DATA_DIR, 'entity_log.csv')
CONFIG_FILE = os.path.join(DATA_DIR, 'config.json')

_config = {'entities': [], 'interval': 60}
_logging = False
_thread = None
_lock = threading.Lock()


def _load():
    global _config
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                _config.update(json.load(f))
        except Exception:
            pass


def _save():
    with open(CONFIG_FILE, 'w') as f:
        json.dump(_config, f, indent=2)


def _headers():
    return {'Authorization': f'Bearer {HA_TOKEN}'}


def _state(entity_id):
    try:
        r = requests.get(f'{HA_URL}/states/{entity_id}', headers=_headers(), timeout=5)
        return r.json().get('state', 'unknown') if r.ok else 'error'
    except Exception:
        return 'error'


def _has_space():
    st = os.statvfs(DATA_DIR)
    return st.f_bavail * st.f_frsize > 1 << 20  # require > 1 MB free


def _run():
    global _logging
    while _logging:
        with _lock:
            entities = list(_config['entities'])
            interval = _config['interval']

        if entities and _has_space():
            ts = datetime.now().isoformat()
            row = [ts] + [_state(e) for e in entities]
            new_file = not os.path.exists(CSV_FILE)
            with open(CSV_FILE, 'a', newline='') as f:
                w = csv.writer(f)
                if new_file:
                    w.writerow(['timestamp'] + entities)
                w.writerow(row)

        deadline = time.monotonic() + interval
        while _logging and time.monotonic() < deadline:
            time.sleep(0.5)


def _start():
    global _logging, _thread
    if not _logging:
        _logging = True
        _thread = threading.Thread(target=_run, daemon=True)
        _thread.start()


def _stop():
    global _logging
    _logging = False


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/entities')
def api_entities():
    try:
        r = requests.get(f'{HA_URL}/states', headers=_headers(), timeout=15)
        r.raise_for_status()
        result = []
        for s in r.json():
            result.append({
                'id': s['entity_id'],
                'state': s['state'],
                'name': s.get('attributes', {}).get('friendly_name') or s['entity_id']
            })
        result.sort(key=lambda x: x['id'])
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/config', methods=['GET'])
def api_get_config():
    with _lock:
        return jsonify({**_config, 'logging': _logging})


@app.route('/api/config', methods=['POST'])
def api_set_config():
    data = request.get_json()
    new_entities = [str(e) for e in data.get('entities', [])]
    new_interval = max(10, int(data.get('interval', 60)))

    with _lock:
        changed = sorted(new_entities) != sorted(_config['entities'])
        _config['entities'] = new_entities
        _config['interval'] = new_interval
        _save()

    # Back up old CSV when entity set changes to avoid mismatched columns
    if changed and os.path.exists(CSV_FILE):
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        os.rename(CSV_FILE, os.path.join(DATA_DIR, f'entity_log_backup_{ts}.csv'))

    _stop()
    if new_entities:
        _start()

    return jsonify({'ok': True})


@app.route('/api/status')
def api_status():
    rows, size = 0, 0
    if os.path.exists(CSV_FILE):
        size = os.path.getsize(CSV_FILE)
        try:
            with open(CSV_FILE) as f:
                rows = max(0, sum(1 for _ in f) - 1)
        except Exception:
            pass

    st = os.statvfs(DATA_DIR)
    free = st.f_bavail * st.f_frsize

    return jsonify({'logging': _logging, 'rows': rows, 'size_bytes': size, 'free_bytes': free})


@app.route('/download')
def download():
    if os.path.exists(CSV_FILE):
        return send_file(
            CSV_FILE,
            as_attachment=True,
            download_name='entity_log.csv',
            mimetype='text/csv'
        )
    return 'No data yet', 404


@app.route('/api/clear', methods=['POST'])
def api_clear():
    _stop()
    if os.path.exists(CSV_FILE):
        os.remove(CSV_FILE)
    with _lock:
        if _config['entities']:
            _start()
    return jsonify({'ok': True})


if __name__ == '__main__':
    os.makedirs(DATA_DIR, exist_ok=True)
    _load()
    if _config['entities']:
        _start()
    app.run(host='0.0.0.0', port=8099, threaded=True)
