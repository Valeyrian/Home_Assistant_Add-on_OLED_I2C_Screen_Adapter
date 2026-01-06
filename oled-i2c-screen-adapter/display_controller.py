import os
import sys
import time
import signal
import threading
import socket
import subprocess
import psutil
import netifaces
import qrcode
import paho.mqtt.client as mqtt
from datetime import datetime
from PIL import Image

# Vérification des imports critiques
try:
    from luma.core.interface.serial import i2c
    from luma.core.render import canvas
    from luma.oled.device import ssd1306, sh1106
except ImportError:
    print("ERREUR CRITIQUE: Bibliothèques luma.oled manquantes.")
    sys.exit(1)

# --- CONFIGURATION CENTRALISÉE ---
class Config:
    """Charge la configuration depuis les variables d'environnement"""
    # MQTT
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

    # Écran et I2C
    I2C_ADDRESS = int(os.getenv('I2C_ADDRESS', '0x3C'), 16)
    I2C_PORT = int(os.getenv('I2C_PORT', 1))
    WIDTH = int(os.getenv('DISPLAY_WIDTH', 128))
    HEIGHT = int(os.getenv('DISPLAY_HEIGHT', 64))
    TYPE = os.getenv('DISPLAY_TYPE', 'ssd1306')
    
    # Paramètres
    REFRESH_INTERVAL = int(os.getenv('REFRESH_INTERVAL', 5))
    DEFAULT_BRIGHTNESS = int(os.getenv('DEFAULT_BRIGHTNESS', 255))
    QR_LINK = os.getenv('QR_LINK', 'http://homeassistant.local:8123/')


# --- MONITORING SYSTÈME ---
class SystemMonitor:
    """Gère la récupération des données système (CPU, RAM, Réseau)"""
    def __init__(self):
        # Premier appel pour initialiser le compteur CPU (évite le premier 0.0)
        psutil.cpu_percent(interval=None)

    def get_system_info(self):
        try:
            # CPU (Non-bloquant grâce à interval=None)
            cpu_percent = psutil.cpu_percent(interval=None)
            
            # Température (Spécifique RPi)
            cpu_temp = None
            try:
                with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                    cpu_temp = float(f.read()) / 1000.0
            except FileNotFoundError:
                pass

            # Mémoire & Disque
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            # Uptime
            uptime = time.time() - psutil.boot_time()
            hours = int(uptime // 3600)
            minutes = int((uptime % 3600) // 60)

            return {
                'cpu': cpu_percent,
                'temp': cpu_temp,
                'ram': memory.percent,
                'disk': (disk.used / disk.total) * 100,
                'up_h': hours,
                'up_m': minutes
            }
        except Exception as e:
            print(f"Erreur SystemMonitor: {e}")
            return None

    def get_network_info(self):
        info = {'ip': 'N/A', 'online': False, 'ping': None, 'ifaces': {}}
        try:
            # 1. IP Locale via socket (plus fiable que le parsing)
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                info['ip'] = s.getsockname()[0]
                s.close()
            except:
                pass

            # 2. Interfaces (filtrage eth/wlan)
            try:
                for iface in netifaces.interfaces():
                    if iface.startswith(('eth', 'wlan', 'en', 'wl')):
                        addrs = netifaces.ifaddresses(iface)
                        if netifaces.AF_INET in addrs:
                            info['ifaces'][iface] = addrs[netifaces.AF_INET][0]['addr']
            except:
                pass

            # 3. Ping Google DNS (Timeout court)
            try:
                res = subprocess.run(['ping', '-c', '1', '-W', '1', '8.8.8.8'], 
                                   capture_output=True, text=True)
                info['online'] = (res.returncode == 0)
                if info['online']:
                    # Extraction du temps de latence
                    for line in res.stdout.split('\n'):
                        if 'time=' in line:
                            time_part = line.split('time=')[1]
                            info['ping'] = float(time_part.split()[0])
                            break
            except:
                pass

            return info
        except Exception as e:
            print(f"Erreur NetworkMonitor: {e}")
            return None


# --- GESTION DE L'ÉCRAN OLED ---
class OLEDManager:
    """Wrapper pour la bibliothèque luma.oled"""
    def __init__(self):
        self.device = None
        self._init_device()

    def _init_device(self):
        try:
            serial_interface = i2c(port=Config.I2C_PORT, address=Config.I2C_ADDRESS)
            if Config.TYPE == 'sh1106':
                self.device = sh1106(serial_interface, width=Config.WIDTH, height=Config.HEIGHT)
            else:
                self.device = ssd1306(serial_interface, width=Config.WIDTH, height=Config.HEIGHT)
            
            self.device.contrast(Config.DEFAULT_BRIGHTNESS)
            print(f"OLED initialisé: {Config.TYPE} ({Config.WIDTH}x{Config.HEIGHT}) sur I2C-{Config.I2C_PORT}")
        except Exception as e:
            print(f"ERREUR INIT OLED: {e}. Vérifiez les connexions I2C.")

    def clear(self):
        if self.device: self.device.clear()
    
    def set_contrast(self, val):
        if self.device: self.device.contrast(val)
        
    def power(self, on=True):
        if self.device:
            self.device.show() if on else self.device.hide()


# --- MOTEUR DE RENDU ---
class ScreenRenderer:
    """Dessine les différents écrans sur le canvas"""
    def __init__(self, width, height):
        self.w = width
        self.h = height
        self.monitor = SystemMonitor()
        self.qr_img = self._generate_qr()

    def _generate_qr(self):
        try:
            qr = qrcode.QRCode(box_size=2, border=1)
            qr.add_data(Config.QR_LINK)
            qr.make(fit=True)
            img = qr.make_image(fill_color="white", back_color="black").convert('1')
            
            # Redimensionnement intelligent
            scale = min(self.w / img.width, self.h / img.height)
            if scale != 1:
                new_size = (int(img.width * scale), int(img.height * scale))
                img = img.resize(new_size, Image.NEAREST) # NEAREST garde les pixels nets
            return img
        except Exception:
            return None

    def _header(self, draw, text):
        draw.rectangle((0, 0, self.w-1, 10), fill="white")
        draw.text((2, -1), text, fill="black")

    def _bar(self, draw, x, y, w, h, val, max_val=100):
        draw.rectangle((x, y, x+w, y+h), outline="white", fill="black")
        pct = max(0, min(val, max_val)) / max_val
        fill = int(pct * (w-2))
        if fill > 0:
            draw.rectangle((x+1, y+1, x+1+fill, y+h-1), fill="white")

    def render_system(self, draw):
        info = self.monitor.get_system_info()
        if not info: return
        self._header(draw, "SYSTEME")
        
        # CPU & RAM
        draw.text((0, 12), f"CPU: {info['cpu']:.0f}%", fill="white")
        self._bar(draw, 50, 14, 70, 6, info['cpu'])
        
        draw.text((0, 22), f"RAM: {info['ram']:.0f}%", fill="white")
        self._bar(draw, 50, 24, 70, 6, info['ram'])

        # Info sup
        temp = f"{info['temp']:.0f}C" if info['temp'] else "?"
        draw.text((0, 32), f"T:{temp}  D:{info['disk']:.0f}%", fill="white")
        draw.text((0, 42), f"Up: {info['up_h']}h {info['up_m']}m", fill="white")
        draw.text((0, 52), datetime.now().strftime("%H:%M:%S"), fill="white")

    def render_network(self, draw):
        info = self.monitor.get_network_info()
        if not info: return
        self._header(draw, "RESEAU")
        
        y = 12
        draw.text((0, y), f"IP: {info['ip']}", fill="white")
        y += 10
        
        for iface, addr in info['ifaces'].items():
            if iface != 'lo' and y < 50:
                draw.text((0, y), f"{iface[:3]}: {addr}", fill="white")
                y += 10
        
        stat = "OK" if info['online'] else "KO"
        ping = f"{info['ping']:.0f}ms" if info['ping'] else ""
        draw.text((0, 52), f"Net: {stat} {ping}", fill="white")

    def render_qr(self, draw):
        if self.qr_img:
            # Centrage
            x = (self.w - self.qr_img.width) // 2
            y = (self.h - self.qr_img.height) // 2
            draw.bitmap((x, y), self.qr_img, fill="white")
        else:
            draw.text((10, 25), "Erreur QR", fill="white")

    def render_text(self, draw, text):
        self._header(draw, "MESSAGE")
        lines = text.split('\n')
        y = 12
        for line in lines[:5]:
            draw.text((0, y), line, fill="white")
            y += 10

    def render_logo(self, draw):
        # Écran de veille / Logo HA
        draw.text((20, 20), "HOME\nASSISTANT", fill="white", align="center")


# --- CONTRÔLEUR PRINCIPAL ---
class DisplayController:
    def __init__(self):
        self.running = True
        self.oled = OLEDManager()
        self.renderer = ScreenRenderer(Config.WIDTH, Config.HEIGHT)
        
        # État
        self.mode = "auto"
        self.text_buffer = "En attente..."
        self.refresh = Config.REFRESH_INTERVAL
        
        # Rotation automatique
        self.screens = ['system', 'network', 'qr']
        self.screen_idx = 0
        
        # MQTT (Utilisation explicite de l'API v1 pour compatibilité)
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        if Config.MQTT_USER:
            self.client.username_pw_set(Config.MQTT_USER, Config.MQTT_PASSWORD)

    def start(self):
        """Démarrage du service"""
        print("Démarrage du Display Controller...")
        
        # Thread MQTT séparé pour ne pas bloquer si le broker est down
        threading.Thread(target=self._mqtt_loop, daemon=True).start()

        # Boucle d'affichage principale
        while self.running:
            self._update_display()
            time.sleep(self.refresh)

    def stop(self, signum=None, frame=None):
        """Arrêt propre"""
        print(f"Arrêt demandé (Signal: {signum})")
        self.running = False
        self.oled.clear()
        sys.exit(0)

    def _mqtt_loop(self):
        """Gère la connexion et reconnexion MQTT infinie"""
        while self.running:
            try:
                print(f"Connexion MQTT à {Config.MQTT_BROKER}...")
                self.client.connect(Config.MQTT_BROKER, Config.MQTT_PORT, 60)
                self.client.loop_forever() # Bloquant tant que connecté
            except Exception as e:
                print(f"Erreur MQTT: {e}. Nouvelle tentative dans 10s...")
                time.sleep(10)

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print("MQTT Connecté !")
            for topic in Config.MQTT_TOPICS.values():
                client.subscribe(topic)
        else:
            print(f"MQTT Refusé code: {rc}")

    def _on_message(self, client, userdata, msg):
        try:
            topic = msg.topic
            payload = msg.payload.decode('utf-8')
            t = Config.MQTT_TOPICS

            if topic == t['text']:
                self.mode = "manual"
                self.text_buffer = payload
                self._update_display() # Rafraîchissement immédiat
            
            elif topic == t['mode']:
                if payload in ['auto', 'manual', 'system', 'network', 'qr', 'off']:
                    self.mode = payload
                    self._update_display()

            elif topic == t['command']:
                if payload == 'clear': self.oled.clear()
                elif payload == 'on': self.oled.power(True)
                elif payload == 'off': self.oled.power(False)

            elif topic == t['brightness']:
                self.oled.set_contrast(int(payload))

            elif topic == t['refresh']:
                self.refresh = int(payload)

        except Exception as e:
            print(f"Erreur commande MQTT: {e}")

    def _update_display(self):
        if not self.oled.device: return

        # Logique de rotation
        target = self.mode
        if self.mode == 'auto':
            target = self.screens[self.screen_idx]
            self.screen_idx = (self.screen_idx + 1) % len(self.screens)

        # Dessin
        with canvas(self.oled.device) as draw:
            if target == 'system':
                self.renderer.render_system(draw)
            elif target == 'network':
                self.renderer.render_network(draw)
            elif target == 'qr':
                self.renderer.render_qr(draw)
            elif target == 'manual':
                self.renderer.render_text(draw, self.text_buffer)
            elif target == 'off':
                self.oled.clear()


# --- POINT D'ENTRÉE ---
if __name__ == "__main__":
    controller = DisplayController()
    
    # Capture des signaux d'arrêt (Stop HA, Ctrl+C)
    signal.signal(signal.SIGTERM, controller.stop)
    signal.signal(signal.SIGINT, controller.stop)
    
    try:
        controller.start()
    except Exception as e:
        print(f"Crash Fatal: {e}")
        controller.stop()