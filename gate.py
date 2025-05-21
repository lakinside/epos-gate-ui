import sys
import requests
import time
import json

gate_id = 1 # номер входа реле
camera_id = 2 # для записи в историю, если несколько камер на одном шлагбауме
epos_camid = 'XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX' # camId из настроек в Е-Поселке
epos_token = 'XXXXXXXXXXXXX' # токен из настроек модуля Управления пропусками
epos_lk_name = '' # имя поселка после lk (lk-NAME.e-poselok.ru)
relay_token = 'XXXXXXXXXXXXX' # пароль от реле

plate_num = sys.argv[1] # программа распознавания должна запускать этот скрипт, передавая в качестве аргумента распознанный номер 

epos_gate_ui_url = 'http://192.168.1.XXX:5000' # адрес где работает epos-gate-ui
gate_relay_url = 'http://192.168.1.XXX' # адрес реле шлагбаума

try:
    
    data = {
            "gate_id": gate_id,
            "camera_id": camera_id,
            "plate_num": plate_num
        }
    
    response = requests.post(
        f'{epos_gate_ui_url}/api/gate/check_plate',
        headers={"Content-Type": "application/json"},
        data=json.dumps(data),
        timeout=1
    )
    
    print('db success', response.text)

except Exception as e:
    print(e)
   
    response = requests.get(f'https://api.e-poselok.ru/cgi-bin/cars/avtogate.cgi?pnam={ epos_lk_name }&api=1&id={ epos_token }&CamId={ epos_camid }&plate={ plate_num }')

    print('epos successful', response.text)
    if 'isallowaccess=1' in response.text:
        response = requests.get(f'{gate_relay_url}/api/relay?relay={gate_id}&state=1&token={relay_token}')
        print('relay open')
        time.sleep(6)
        locked = False
        try:
            response = requests.get(f'{ epos_gate_ui_url }/api/gate/check_lock?gate_id={gate_id}')
            locked = response.json().get('locked')
            print('relay locked:', locked)
        except Exception as e:
            print(f'''Can't get lock status, prooceeding to close. Error: {e}''')
        if not locked:
            response = requests.get(f'{ epos_gate_ui_url }/api/relay?relay={gate_id}&state=0&token={relay_token}')
            print('relay closed')
    