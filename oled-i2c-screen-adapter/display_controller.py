import os
import paho.mqtt.client as mqtt
import time
import threading
import psutil
import socket
import subprocess
from datetime import datetime
import netifaces
import qrcode
from PIL import Image
import signal
import sys



# --- Configuration MQTT ---
MQTT_BROKER = os.getenv('MQTT_BROKER', 'core-mqtt')
MQTT_PORT = int(os.getenv('MQTT_PORT', 1883))
MQTT_USER = os.getenv('MQTT_USER', 'homeassistant')
MQTT_PASSWORD = os.getenv('MQTT_PASSWORD', '')
# Topics MQTT
MQTT_TOPICS = {
    'text': os.getenv('MQTT_TOPIC_TEXT', 'screen/gme12864/text'),
    'command': os.getenv('MQTT_TOPIC_COMMAND', 'screen/gme12864/command'),
    'mode': os.getenv('MQTT_TOPIC_MODE', 'screen/gme12864/mode'),
    'brightness': os.getenv('MQTT_TOPIC_BRIGHTNESS', 'screen/gme12864/brightness'),
    'refresh': os.getenv('MQTT_TOPIC_REFRESH', 'screen/gme12864/refresh')
}

# Configuration écran
I2C_ADDRESS = int(os.getenv('I2C_ADDRESS', '0x3C'), 16)
I2C_PORT = int(os.getenv('I2C_PORT', 1))
DISPLAY_WIDTH = int(os.getenv('DISPLAY_WIDTH', 128))
DISPLAY_HEIGHT = int(os.getenv('DISPLAY_HEIGHT', 64))
DISPLAY_TYPE = os.getenv('DISPLAY_TYPE', 'ssd1306')
refresh_interval = int(os.getenv('REFRESH_INTERVAL', 5))
brightness = int(os.getenv('DEFAULT_BRIGHTNESS', 255))
print(f"Configuration MQTT: {MQTT_BROKER}:{MQTT_PORT} User:{MQTT_USER}")

# Configuration tierce

QR_LINK = os.getenv('QR_LINK', 'http://homeassistant.local:8123/')

# --- Initialisation de l'écran OLED ---
device = None
try:
    from luma.core.interface.serial import i2c, spi
    from luma.core.render import canvas
    from luma.oled.device import ssd1306, sh1106
    from luma.core.legacy import text, textsize
    from luma.core.legacy.font import proportional, CP437_FONT, TINY_FONT, LCD_FONT
    from PIL import Image, ImageDraw, ImageFont

    serial = i2c(port=I2C_PORT, address=I2C_ADDRESS)

    if DISPLAY_TYPE == 'sh1106':
        device = sh1106(serial, width=DISPLAY_WIDTH, height=DISPLAY_HEIGHT)
    else:
        device = ssd1306(serial, width=DISPLAY_WIDTH, height=DISPLAY_HEIGHT)

    device.contrast(brightness)

    print(f"Écran {DISPLAY_TYPE} {DISPLAY_WIDTH}x{DISPLAY_HEIGHT} initialisé sur I2C {I2C_PORT}:0x{I2C_ADDRESS:02X}")
except ImportError as e:
    print(f"ATTENTION: Bibliothèque manquante: {e}")
    print("Installez avec: pip install luma.oled psutil netifaces")
except Exception as e:
    print(f"Erreur lors de l'initialisation de l'écran I2C: {e}")


# --- Variables globales ---
current_mode = "auto"  # auto, manual, system, network, sensors, qr
manual_text = ""
refresh_interval = 5  # secondes
brightness = 255
display_running = True
current_screen = 0

# --- Fonctions d'informations système ---
def get_system_info():
    """Récupère les informations système"""
    try:
        # CPU
        cpu_percent = psutil.cpu_percent(interval=1)
        cpu_temp = None
        try:
            # Température CPU (Raspberry Pi)
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                cpu_temp = float(f.read()) / 1000.0
        except:
            pass
        
        # Mémoire
        memory = psutil.virtual_memory()
        memory_percent = memory.percent
        
        # Stockage
        disk = psutil.disk_usage('/')
        disk_percent = (disk.used / disk.total) * 100
        
        # Temps de fonctionnement
        boot_time = psutil.boot_time()
        uptime = time.time() - boot_time
        uptime_hours = int(uptime // 3600)
        uptime_minutes = int((uptime % 3600) // 60)
        
        return {
            'cpu_percent': cpu_percent,
            'cpu_temp': cpu_temp,
            'memory_percent': memory_percent,
            'disk_percent': disk_percent,
            'uptime_hours': uptime_hours,
            'uptime_minutes': uptime_minutes
        }
    except Exception as e:
        print(f"Erreur lors de la récupération des infos système: {e}")
        return None

def get_network_info():
    """Récupère les informations réseau"""
    try:
        network_info = {}
        
        # Adresse IP principale
        try:
            # Méthode 1: via socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            network_info['local_ip'] = local_ip
        except:
            network_info['local_ip'] = "N/A"
        
        # Interfaces réseau
        interfaces = netifaces.interfaces()
        for interface in interfaces:
            if interface.startswith('eth') or interface.startswith('wlan'):
                addrs = netifaces.ifaddresses(interface)
                if netifaces.AF_INET in addrs:
                    network_info[interface] = addrs[netifaces.AF_INET][0]['addr']
        
        # Test de connectivité
        try:
            result = subprocess.run(['ping', '-c', '1', '8.8.8.8'], 
                                  capture_output=True, timeout=3)
            network_info['internet'] = result.returncode == 0
            
            # Latence
            if network_info['internet']:
                output = result.stdout.decode()
                for line in output.split('\n'):
                    if 'time=' in line:
                        ping_time = line.split('time=')[1].split()[0]
                        network_info['ping'] = float(ping_time)
                        break
        except:
            network_info['internet'] = False
            network_info['ping'] = None
        
        return network_info
    except Exception as e:
        print(f"Erreur lors de la récupération des infos réseau: {e}")
        return None

def get_ha_sensors():
    """Récupère les capteurs depuis Home Assistant via MQTT"""
    # Cette fonction pourrait être étendue pour récupérer des capteurs spécifiques
    # Pour l'instant, on retourne des données d'exemple
    return {
        'temperature_ext': 22.5,
        'humidite': 65,
        'luminosite': 850
    }

# --- Fonctions d'affichage ---
def draw_header(draw, title):
    """Dessine l'en-tête avec le titre"""
    draw.rectangle((0, 0, 127, 12), fill="white")
    draw.text((2, 2), title, fill="black")
    draw.line((0, 13, 127, 13), fill="white")

def draw_progress_bar(draw, x, y, width, height, value, max_value=100):
    """Dessine une barre de progression"""
    # Cadre
    draw.rectangle((x, y, x+width, y+height), outline="white", fill="black")
    # Barre de progression
    fill_width = int((value / max_value) * (width-2))
    if fill_width > 0:
        draw.rectangle((x+1, y+1, x+1+fill_width, y+height-1), fill="white")

def display_system_screen(draw):
    """Affiche l'écran système"""
    info = get_system_info()
    if not info:
        draw.text((10, 30), "Erreur système", fill="white")
        return
    
    draw_header(draw, "SYSTEME")
    
    # CPU
    draw.text((2, 18), f"CPU: {info['cpu_percent']:.1f}%", fill="white")
    draw_progress_bar(draw, 50, 18, 40, 8, info['cpu_percent'])
    
    if info['cpu_temp']:
        draw.text((95, 18), f"{info['cpu_temp']:.1f}°C", fill="white")
    
    # Mémoire
    draw.text((2, 30), f"RAM: {info['memory_percent']:.1f}%", fill="white")
    draw_progress_bar(draw, 50, 30, 40, 8, info['memory_percent'])
    
    # Stockage
    draw.text((2, 42), f"Disk: {info['disk_percent']:.1f}%", fill="white")
    draw_progress_bar(draw, 50, 42, 40, 8, info['disk_percent'])
    
    # Uptime
    draw.text((2, 54), f"Up: {info['uptime_hours']}h{info['uptime_minutes']:02d}m", fill="white")
    
    # Heure
    now = datetime.now()
    time_str = now.strftime("%H:%M:%S")
    draw.text((70, 54), time_str, fill="white")

def display_network_screen(draw):
    """Affiche l'écran réseau"""
    info = get_network_info()
    if not info:
        draw.text((10, 30), "Erreur réseau", fill="white")
        return
    
    draw_header(draw, "RESEAU")
    
    y = 18
    
    # IP locale
    if 'local_ip' in info:
        draw.text((2, y), f"IP: {info['local_ip']}", fill="white")
        y += 12
    
    # Interfaces
    for interface, ip in info.items():
        if interface.startswith('eth') or interface.startswith('wlan'):
            draw.text((2, y), f"{interface}: {ip}", fill="white")
            y += 10
            if y > 50:
                break
    
    # Connectivité Internet
    if 'internet' in info:
        status = "OK" if info['internet'] else "KO"
        draw.text((2, 54), f"Internet: {status}", fill="white")
        
        if info.get('ping'):
            draw.text((70, 54), f"Ping: {info['ping']:.1f}ms", fill="white")

def display_sensors_screen(draw):
    """Affiche l'écran capteurs"""
    sensors = get_ha_sensors()
    
    draw_header(draw, "CAPTEURS")
    
    y = 18
    
    # Température extérieure
    draw.text((2, y), f"Temp ext: {sensors['temperature_ext']:.1f}°C", fill="white")
    y += 12
    
    # Humidité
    draw.text((2, y), f"Humidite: {sensors['humidite']}%", fill="white")
    draw_progress_bar(draw, 80, y, 40, 8, sensors['humidite'])
    y += 12
    
    # Luminosité
    draw.text((2, y), f"Lum: {sensors['luminosite']} lux", fill="white")
    
    # Date
    now = datetime.now()
    date_str = now.strftime("%d/%m/%Y")
    draw.text((2, 54), date_str, fill="white")

def display_qr_screen(draw):
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=4,   # petit box size pour avoir une image petite
        border=1,
    )
    qr.add_data(QR_LINK)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="white", back_color="black").convert('1')
    
    # Taille max (avec marge)
    max_width = DISPLAY_WIDTH - 4
    max_height = DISPLAY_HEIGHT - 4
    
    img_width, img_height = img.size

    scale = min(max_width / img_width, max_height / img_height, 1)
 
    new_size = (int(img_width * scale), int(img_height * scale))
    
    img = img.resize(new_size, resample=Image.NEAREST)
    
    pos_x = (DISPLAY_WIDTH - new_size[0]) // 2
    pos_y = (DISPLAY_HEIGHT - new_size[1]) // 2

    pixels = img.load()
    for y in range(new_size[1]):
        for x in range(new_size[0]):
            # pixel vaut 0 (noir) ou 255 (blanc)
            if pixels[x, y] == 255:
                draw.point((pos_x + x, pos_y + y), fill=1)
            else:
                # Optionnel : dessiner en noir (effacer)
                draw.point((pos_x + x, pos_y + y), fill=0)

def display_manual_text(draw, text):
    """Affiche du texte manuel"""
    draw_header(draw, "MESSAGE")
    
    # Découpe le texte en lignes
    lines = text.split('\n')
    y = 18
    for line in lines[:4]:  # Max 4 lignes
        draw.text((2, y), line[:20], fill="white")  # Max 20 caractères
        y += 12

def update_display_content():
    """Met à jour le contenu de l'écran"""
    global current_screen
    
    if not device:
        return
    
    try:
        with canvas(device) as draw:
            if current_mode == "manual":
                display_manual_text(draw, manual_text)
            elif current_mode == "system":
                display_system_screen(draw)
            elif current_mode == "network":
                display_network_screen(draw)
            elif current_mode == "sensors":
                display_sensors_screen(draw)
            elif current_mode == "qr":
                display_qr_screen(draw)
            elif current_mode == "auto":
                # Rotation automatique entre les écrans
                screens = [display_system_screen, display_network_screen, display_sensors_screen, display_qr_screen]
                screens[current_screen % len(screens)](draw)
                current_screen += 1
            
    except Exception as e:
        print(f"Erreur lors de l'affichage: {e}")

# --- Thread d'affichage ---
def display_thread():
    """Thread principal d'affichage"""
    global display_running
    
    while display_running:
        update_display_content()
        time.sleep(refresh_interval)

# --- Fonctions MQTT ---
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Connecté au broker MQTT!")
        for topic in MQTT_TOPICS.values():
            client.subscribe(topic)
        print(f"Abonné aux topics: {list(MQTT_TOPICS.values())}")
    else:
        print(f"Échec de la connexion MQTT, code {rc}")

def on_message(client, userdata, msg):
    global current_mode, manual_text, refresh_interval, brightness
    
    try:
        topic = msg.topic
        payload = msg.payload.decode("utf-8")
        print(f"Message reçu sur {topic}: {payload}")
        
        if topic == MQTT_TOPICS['text']:
            current_mode = "manual"
            manual_text = payload
            update_display_content()
            
        elif topic == MQTT_TOPICS['command']:
            if payload == "clear":
                if device:
                    device.clear()
                    print("Écran effacé.")
            elif payload == "power_off":
                if device:
                    device.hide()
                    print("Écran éteint.")
            elif payload == "power_on":
                if device:
                    device.show()
                    print("Écran allumé.")
            else:
                print(f"Commande non reconnue: {payload}")
                
        elif topic == MQTT_TOPICS['mode']:
            if payload in ["auto", "manual", "system", "network", "sensors", "qr"]:
                current_mode = payload
                print(f"Mode changé vers: {current_mode}")
                update_display_content()
                
        elif topic == MQTT_TOPICS['brightness']:
            try:
                brightness = int(payload)
                if device:
                    device.contrast(brightness)
                print(f"Luminosité changée: {brightness}")
            except ValueError:
                print("Valeur de luminosité invalide")
                
        elif topic == MQTT_TOPICS['refresh']:
            try:
                refresh_interval = int(payload)
                print(f"Intervalle de rafraîchissement: {refresh_interval}s")
            except ValueError:
                print("Valeur d'intervalle invalide")
                
    except Exception as e:
        print(f"Erreur lors du traitement du message MQTT: {e}")


def handle_exit(sig, frame):
    """Gère l'arrêt propre de l'add-on lors d'un signal SIGTERM ou SIGINT"""
    print(f"Signal d'arrêt reçu ({sig}). Fermeture en cours...")
    global display_running
    display_running = False # Arrête la boucle du thread d'affichage
    
    if device:
        try:
            device.clear()
            print("Écran OLED effacé.")
        except Exception as e:
            print(f"Erreur lors de l'effacement de l'écran : {e}")
    
    # Sortie propre du script
    sys.exit(0)

# Enregistre le gestionnaire pour SIGTERM (arrêt propre Docker) et SIGINT (Ctrl+C)
signal.signal(signal.SIGTERM, handle_exit)
signal.signal(signal.SIGINT, handle_exit)


# --- Démarrage ---
def main():
    global display_running
    
    # Configuration MQTT
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    
    if MQTT_USER and MQTT_PASSWORD:
        client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
    
    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
    except Exception as e:
        print(f"Impossible de se connecter au broker MQTT: {e}")
        return
    
    # Démarrage du thread d'affichage
    display_thread_obj = threading.Thread(target=display_thread)
    display_thread_obj.daemon = True
    display_thread_obj.start()
    
    print("Écran OLED démarré - Mode automatique activé")
    print("Topics MQTT disponibles:")
    for name, topic in MQTT_TOPICS.items():
        print(f"  {name}: {topic}")
    
    try:
        # Boucle principale MQTT
        client.loop_forever()
    except Exception as e:
        print(f"Erreur boucle MQTT: {e}")
        handle_exit(signal.SIGTERM, None)

if __name__ == "__main__":
    main()
