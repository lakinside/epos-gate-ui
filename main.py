import sqlite3
import json
from flask import Flask, render_template, request, jsonify
import requests
from datetime import datetime, timedelta
import threading
import time
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
DATABASE = 'license_plates.db'
GATES = {
    1: {'state': False, 'name': 'Gate 1', 'locked': False},  # False = closed, True = open
    2: {'state': False, 'name': 'Gate 2', 'locked': False},
    3: {'state': False, 'name': 'Gate 3', 'locked': False}
}

epos_camid = 'XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX' # camId из настроек в Е-Поселке
epos_token = 'XXXXXXXXXXXXX' # токен из настроек модуля Управления пропусками
epos_lk_name = '' # имя поселка после lk (lk-NAME.e-poselok.ru)
relay_token = 'XXXXXXXXXXXXX' # пароль от реле
epos_gate_ui_url = 'http://192.168.1.XXX:5000' # адрес где работает epos-gate-ui
gate_relay_url = 'http://192.168.1.XXX' # адрес реле шлагбаума
socol_url = 'http://192.168.1.XXX:80' # адрес вызывной панели домофона Сокол
socol_login = '' # логин и пароль от панели домофона
socol_pass = ''

# Initialize database
def init_db():
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()

        # Create temporary_plates table if it doesn't exist
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS temporary_plates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                house_number TEXT(20),
                plate_number TEXT(10),
                description TEXT(200),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_temporary_plates_created_at 
            ON temporary_plates(created_at)
        ''')

        # Create long_term_plates table if it doesn't exist
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS long_term_plates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                house_number TEXT(20),
                plate_number TEXT(20),
                last_updated TIMESTAMP
            )
        ''')

        # Create plate recognition log table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS plate_recognition_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plate_number TEXT,
                gate_id INTEGER,
                camera_id INTEGER,
                recognition_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                access_granted BOOLEAN,
                source TEXT  -- 'temporary' or 'long-term'
            )
        ''')

        conn.commit()


# Clear temporary plates daily at 3:00 AM
def clear_temporary_plates():
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM temporary_plates")
        conn.commit()
    print(f"Temporary plates cleared at {datetime.now()}")


# Background task to update long-term plates
def update_long_term_plates():
    try:
        print("Updating long-term plates...")
        response = requests.get(
            f"https://lk-{ epos_lk_name }.e-poselok.ru/cgi-bin/standalone/avto.cgi?action={ epos_token }&gate=0&json=1",
            timeout=10
        )
        data = response.json()

        plates = []
        # Process AllowedList1
        for item in data.get('AllowedList1', []):
            plates.append((item['Number'], item['Plate']))

        # Process AllowedList2
        for item in data.get('AllowedList2', []):
            plates.append((item['Number'], item['Plate']))

        if plates:
        # Update database
            with sqlite3.connect(DATABASE) as conn:
                cursor = conn.cursor()
                # Clear existing long-term plates
                cursor.execute("DELETE FROM long_term_plates")
                # Insert new ones
                cursor.executemany(
                    "INSERT INTO long_term_plates (house_number, plate_number, last_updated) VALUES (?, ?, ?)",
                    [(house, plate, datetime.now()) for house, plate in plates]
                )
                conn.commit()
        print(f"Updated {len(plates)} long-term plates at {datetime.now()}")
    except Exception as e:
        print(f"Error updating long-term plates: {e}")


# Start scheduled tasks
def start_scheduled_tasks():
    scheduler = BackgroundScheduler()
    # Update long-term plates every hour
    scheduler.add_job(update_long_term_plates, 'interval', hours=1)
    scheduler.start()


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/search', methods=['GET'])
def search_plates():
    search_term = request.args.get('term', '').strip()

    if len(search_term) < 3:
        return jsonify({'temporary': [], 'long_term': []})

    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()

        # Search temporary plates by plate number only
        cursor.execute('''
            SELECT house_number, plate_number, description 
            FROM temporary_plates 
            WHERE plate_number LIKE ?
              AND date(created_at) = date('now')
            ORDER BY created_at DESC
        ''', (f'%{search_term}%',))
        temp_results = cursor.fetchall()

        # Search long-term plates by plate number only
        cursor.execute('''
            SELECT house_number, plate_number 
            FROM long_term_plates 
            WHERE plate_number LIKE ?
        ''', (f'%{search_term}_%',))
        long_term_results = cursor.fetchall()

    # Format results
    temp_formatted = [{
        'house_number': row[0],
        'plate_number': row[1],
        'description': row[2],
        'type': 'temporary'
    } for row in temp_results]

    long_term_formatted = [{
        'house_number': row[0],
        'plate_number': row[1],
        'type': 'long-term'
    } for row in long_term_results]

    return jsonify({'temporary': temp_formatted, 'long_term': long_term_formatted})

@app.route('/add_temporary', methods=['POST'])
def add_temporary_plate():
    house_number = request.form.get('house_number', '').strip()
    plate_number = request.form.get('plate_number', '').strip().upper()
    description = request.form.get('description', '').strip()

    if not house_number or not plate_number:
        return jsonify({'success': False, 'error': 'House number and plate number are required'})

    # Validate plate number is exactly 3 digits
    if not (plate_number.isdigit() and len(plate_number) == 3):
        return jsonify({'success': False, 'error': 'Plate number must be exactly 3 digits'})

    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        try:
            # Explicitly specify columns to avoid any schema issues
            cursor.execute('''
                INSERT INTO temporary_plates (house_number, plate_number, description)
                VALUES (?, ?, ?)
            ''', (house_number, plate_number, description))
            conn.commit()
            return jsonify({'success': True})
        except sqlite3.Error as e:
            return jsonify({'success': False, 'error': str(e)})


@app.route('/delete_temporary/<int:plate_id>', methods=['DELETE'])
def delete_temporary_plate(plate_id):
    try:
        with sqlite3.connect(DATABASE) as conn:
            cursor = conn.cursor()
            # First check if the plate exists
            cursor.execute('SELECT id FROM temporary_plates WHERE id = ?', (plate_id,))
            if not cursor.fetchone():
                return jsonify({'success': False, 'error': 'Plate not found'}), 404

            # Delete the plate
            cursor.execute('DELETE FROM temporary_plates WHERE id = ?', (plate_id,))
            conn.commit()

            # Verify deletion was successful
            cursor.execute('SELECT id FROM temporary_plates WHERE id = ?', (plate_id,))
            if cursor.fetchone():
                return jsonify({'success': False, 'error': 'Deletion failed'}), 500

            return jsonify({'success': True})
    except sqlite3.Error as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/get_temporary_plates')
def get_temporary_plates():
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''SELECT id, house_number, plate_number, description FROM temporary_plates WHERE date(created_at) = date('now') ORDER BY created_at DESC''')
        plates = cursor.fetchall()

    formatted = [{
        'id': row[0],
        'house_number': row[1],
        'plate_number': row[2],
        'description': row[3]
    } for row in plates]

    return jsonify({'plates': formatted})


@app.route('/api/gates', methods=['GET'])
def get_gates():
    return jsonify(GATES)


@app.route('/api/gates/<int:gate_id>', methods=['POST'])
def set_gate(gate_id):
    if gate_id not in GATES:
        return jsonify({'success': False, 'error': 'Invalid gate ID'}), 400

    data = request.get_json()
    new_state = data.get('state')

    if new_state not in [True, False]:
        return jsonify({'success': False, 'error': 'Invalid state'}), 400

    # Make API call to physical gate controller
    try:
        if gate_id in (1, 2):
            relay_num = gate_id
            state_num = 1 if new_state else 0
            api_url = f"{ gate_relay_url }/api/relay?relay={ relay_num }&state={ state_num }&token={ relay_token }"
            response = requests.get(api_url, timeout=3)
            response.raise_for_status()
        elif gate_id == 3:
            api_url = f'{ socol_url }/relay/1/open'
            session = requests.Session()
            session.auth = (socol_login, socol_pass)
            auth = session.post( socol_url )
            response = session.put(api_url, timeout=3)
            print(response)

            response.raise_for_status()
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

    # Update gate state if API call succeeded
    if gate_id in (1, 2):
        GATES[gate_id]['state'] = new_state
    if new_state and gate_id in (1, 2):
        GATES[gate_id]['locked'] = True
    else:
        GATES[gate_id]['locked'] = False

    return jsonify({'success': True, 'new_state': new_state})


@app.route('/api/gate/check_plate', methods=['POST'])
def check_plate_and_open_gate():
    data = request.get_json()
    gate_id = data.get('gate_id')
    camera_id = data.get('camera_id')
    plate_num = data.get('plate_num')

    if not all([gate_id, camera_id, plate_num]):
        return jsonify({'success': False, 'error': 'Missing parameters'}), 400

    # Search for plate in both tables
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        access_granted = False
        source = None

        cursor.execute('''
            SELECT 1 FROM long_term_plates 
            WHERE plate_number = ?
            LIMIT 1
        ''', (plate_num,))
        if cursor.fetchone():
            access_granted = True
            source = 'long-term'
        print(f'long-term {plate_num} {access_granted}')
        # Check temporary plates if not found in long-term
        if not access_granted:
            # Check temporary plates
            cursor.execute('''
                SELECT 1 FROM temporary_plates 
                WHERE ? like '%' || plate_number || '_%'
                  AND date(created_at) = date('now')
                LIMIT 1
            ''', (plate_num,))
            if cursor.fetchone():
                access_granted = True
                source = 'temporary'
            print(f'long-term {plate_num} {access_granted}')

        if not access_granted:
            response = requests.get(
                f'https://api.e-poselok.ru/cgi-bin/cars/avtogate.cgi?pnam={ epos_lk_name }&api=1&id={ epos_token}&CamId={ epos_camid }&plate={plate_num}')
            access_granted = ('isallowaccess=1' in response.text)
            source = 'e-poselok'
            print(f'e-poselok {plate_num} {access_granted}')

        # Log the recognition attempt
        cursor.execute('''
            INSERT INTO plate_recognition_log 
            (plate_number, gate_id, camera_id, access_granted, source)
            VALUES (?, ?, ?, ?, ?)
        ''', (plate_num, gate_id, camera_id, access_granted, source))
        conn.commit()

        if access_granted and not GATES[gate_id]['locked']:
            # Open the gate
            try:
                # Open gate
                relay_num = gate_id
                api_url = f"{ gate_relay_url }/api/relay?relay={relay_num}&state=1&token={ relay_token }"
                requests.get(api_url, timeout=1)
                # Schedule gate closure after 6 seconds
                def close_gate(relay_num):
                    global GATES
                    if not GATES[relay_num]['locked']:
                        close_url = f"{ gate_relay_url }/api/relay?relay={relay_num}&state=0&token={ relay_token }"
                        requests.get(close_url, timeout=1)
                
                threading.Timer(6, close_gate, args=[relay_num]).start()
                return jsonify({
                    'success': True,
                    'access_granted': True,
                    'gate_opened': True,
                    'source': source
                })

            except Exception as e:
                return jsonify({
                    'success': False,
                    'error': f"Gate control error: {str(e)}",
                    'access_granted': True,
                    'source': source
                }), 500

    return jsonify({
        'success': True,
        'access_granted': False,
        'message': 'Plate not found in database'
    })

@app.route('/api/gate/check_lock', methods=['GET'])
def check_lock():
    gate_id = request.args.get('gate_id')

    if not gate_id or gate_id not in ('1', '2'):
        return jsonify({'success': False, 'error': f'Wrong parameter {gate_id}'}), 400

    return jsonify({'success': True, 'locked': GATES[int(gate_id)]['locked']}), 400

@app.route('/plate_logs')
def plate_logs_page():
    return render_template('plate_logs.html')

@app.route('/api/plate_logs')
def get_plate_logs():
    plate_filter = request.args.get('plate_filter', '').strip()
    
    with sqlite3.connect(DATABASE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        if plate_filter:
            cursor.execute('''
                SELECT id,
                plate_number,
                gate_id,
                camera_id,
                DATETIME(recognition_time , '+3 hours') as recognition_time,
                access_granted,
                source
                 FROM plate_recognition_log
                WHERE plate_number LIKE ?
                ORDER BY recognition_time DESC
                LIMIT 500
            ''', (f'%{plate_filter}_%',))
        else:
            cursor.execute('''
                SELECT  id,
                plate_number,
                gate_id,
                camera_id,
                DATETIME(recognition_time , '+3 hours') as recognition_time,
                access_granted,
                source
                FROM plate_recognition_log
                ORDER BY recognition_time DESC
                LIMIT 500
            ''')
        
        logs = [dict(row) for row in cursor.fetchall()]
    
    return jsonify({'logs': logs})

if __name__ == '__main__':
    init_db()
    start_scheduled_tasks()
    app.run(debug=True, host='0.0.0.0')