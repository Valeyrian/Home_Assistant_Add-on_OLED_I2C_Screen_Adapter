import os
import sys
import time
import signal
import threading
import socket
import subprocess
import logging
import psutil
import netifaces
import qrcode
import paho.mqtt.client as mqtt
from datetime import datetime
from PIL import Image

# Critical imports check
try:
    from luma.core.interface.serial import i2c
    from luma.core.render import canvas
    from luma.oled.device import ssd1306, sh1106
except ImportError:
    # Use a fallback basic logging to stderr if logging isn't set yet
    logging.basicConfig(level=logging.ERROR, format='[%(asctime)s] %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    logging.critical("Critical error: Missing luma.oled libraries.")
    sys.exit(1)

# --- CENTRALIZED CONFIGURATION ---
class Config:
    """Load configuration from environment variables"""
    # MQTT
    MQTT_BROKER = os.getenv('MQTT_BROKER', 'core-mqtt')
    MQTT_PORT = int(os.getenv('MQTT_PORT', 1883))
    MQTT_USER = os.getenv('MQTT_USER', 'homeassistant')
    MQTT_PASSWORD = os.getenv('MQTT_PASSWORD', '')
    
    # MQTT Topics
    MQTT_TOPICS = {
        'text': os.getenv('MQTT_TOPIC_TEXT', 'screen/gme12864/text'),
        'command': os.getenv('MQTT_TOPIC_COMMAND', 'screen/gme12864/command'),
        'mode': os.getenv('MQTT_TOPIC_MODE', 'screen/gme12864/mode'),
        'brightness': os.getenv('MQTT_TOPIC_BRIGHTNESS', 'screen/gme12864/brightness'),
        'refresh': os.getenv('MQTT_TOPIC_REFRESH', 'screen/gme12864/refresh')
    }

    # Display and I2C
    I2C_ADDRESS = int(os.getenv('I2C_ADDRESS', '0x3C'), 16)
    I2C_PORT = int(os.getenv('I2C_PORT', 1))
    WIDTH = int(os.getenv('DISPLAY_WIDTH', 128))
    HEIGHT = int(os.getenv('DISPLAY_HEIGHT', 64))
    TYPE = os.getenv('DISPLAY_TYPE', 'ssd1306')
    
    # Parameters
    REFRESH_INTERVAL = int(os.getenv('REFRESH_INTERVAL', 5))
    DEFAULT_BRIGHTNESS = int(os.getenv('DEFAULT_BRIGHTNESS', 255))
    QR_LINK = os.getenv('QR_LINK', 'http://homeassistant.local:8123/')
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()


# --- LOGGING SETUP ---
_level = getattr(logging, Config.LOG_LEVEL, logging.INFO)
logging.basicConfig(
    level=_level,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


# --- SYSTEM MONITORING ---
class SystemMonitor:
    """Handle system data collection (CPU, RAM, Network)"""
    def __init__(self):
        # First call to initialize CPU counter (avoid initial 0.0)
        psutil.cpu_percent(interval=None)

    def get_system_info(self):
        try:
            # CPU (Non-blocking thanks to interval=None)
            cpu_percent = psutil.cpu_percent(interval=None)
            
            # Temperature (Raspberry Pi specific)
            cpu_temp = None
            try:
                with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                    cpu_temp = float(f.read()) / 1000.0
            except FileNotFoundError:
                pass

            # Memory & Disk
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
            logger.error(f"SystemMonitor error: {e}")
            return None

    def get_network_info(self):
        info = {'ip': 'N/A', 'online': False, 'ping': None, 'ifaces': {}}
        try:
            # 1. Local IP via socket (more robust than parsing)
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                info['ip'] = s.getsockname()[0]
                s.close()
            except:
                pass

            # 2. Interfaces (filter eth/wlan)
            try:
                for iface in netifaces.interfaces():
                    if iface.startswith(('eth', 'wlan', 'en', 'wl')):
                        addrs = netifaces.ifaddresses(iface)
                        if netifaces.AF_INET in addrs:
                            info['ifaces'][iface] = addrs[netifaces.AF_INET][0]['addr']
            except:
                pass

            # 3. Ping Google DNS (short timeout)
            try:
                res = subprocess.run(['ping', '-c', '1', '-W', '1', '8.8.8.8'], 
                                   capture_output=True, text=True)
                info['online'] = (res.returncode == 0)
                if info['online']:
                    # Extract latency
                    for line in res.stdout.split('\n'):
                        if 'time=' in line:
                            time_part = line.split('time=')[1]
                            info['ping'] = float(time_part.split()[0])
                            break
            except:
                pass

            return info
        except Exception as e:
            logger.error(f"NetworkMonitor error: {e}")
            return None


# --- OLED DISPLAY MANAGEMENT ---
class OLEDManager:
    """Wrapper for luma.oled library"""
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
            logger.info(f"OLED initialized: {Config.TYPE} ({Config.WIDTH}x{Config.HEIGHT}) on I2C-{Config.I2C_PORT}")
        except Exception as e:
            logger.error(f"OLED init error: {e}. Check I2C connections.")

    def clear(self):
        if self.device:
            self.device.clear()
            logger.info("OLED cleared")
    
    def set_contrast(self, val):
        if self.device:
            self.device.contrast(val)
            logger.info(f"OLED contrast set to {val}")
        
    def power(self, on=True):
        if self.device:
            self.device.show() if on else self.device.hide()
            logger.info("OLED power ON" if on else "OLED power OFF")


# --- RENDERING ENGINE ---
class ScreenRenderer:
    """Draw various screens on the canvas"""
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
            
            # Smart resizing
            scale = min(self.w / img.width, self.h / img.height)
            if scale != 1:
                new_size = (int(img.width * scale), int(img.height * scale))
                img = img.resize(new_size, Image.NEAREST) # NEAREST keeps pixels sharp
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
        self._header(draw, "SYSTEM")
        
        # CPU & RAM
        draw.text((0, 12), f"CPU: {info['cpu']:.0f}%", fill="white")
        self._bar(draw, 50, 14, 70, 6, info['cpu'])
        
        draw.text((0, 22), f"RAM: {info['ram']:.0f}%", fill="white")
        self._bar(draw, 50, 24, 70, 6, info['ram'])

        # Additional info
        temp = f"{info['temp']:.0f}C" if info['temp'] else "?"
        draw.text((0, 32), f"T:{temp}  D:{info['disk']:.0f}%", fill="white")
        draw.text((0, 42), f"Up: {info['up_h']}h {info['up_m']}m", fill="white")
        draw.text((0, 52), datetime.now().strftime("%H:%M:%S"), fill="white")

    def render_network(self, draw):
        info = self.monitor.get_network_info()
        if not info: return
        self._header(draw, "NETWORK")
        
        y = 12
        draw.text((0, y), f"IP: {info['ip']}", fill="white")
        y += 10
        
        for iface, addr in info['ifaces'].items():
            if iface != 'lo' and y < 50:
                draw.text((0, y), f"{iface[:3]}: {addr}", fill="white")
                y += 10
        
        stat = "OK" if info['online'] else "DOWN"
        ping = f"{info['ping']:.0f}ms" if info['ping'] else ""
        draw.text((0, 52), f"Net: {stat} {ping}", fill="white")

    def render_qr(self, draw):
        if self.qr_img:
            # Centrage
            x = (self.w - self.qr_img.width) // 2
            y = (self.h - self.qr_img.height) // 2
            draw.bitmap((x, y), self.qr_img, fill="white")
        else:
            draw.text((10, 25), "QR Error", fill="white")

    def render_text(self, draw, text):
        self._header(draw, "MESSAGE")
        lines = text.split('\n')
        y = 12
        for line in lines[:5]:
            draw.text((0, y), line, fill="white")
            y += 10

    def render_logo(self, draw):
        # Screensaver / HA Logo
        draw.text((20, 20), "HOME\nASSISTANT", fill="white", align="center")


# --- MAIN CONTROLLER ---
class DisplayController:
    def __init__(self):
        self.running = True
        self.oled = OLEDManager()
        self.renderer = ScreenRenderer(Config.WIDTH, Config.HEIGHT)
        
        # State
        self.mode = "auto"
        self.text_buffer = "Waiting..."
        self.refresh = Config.REFRESH_INTERVAL
        
        # Automatic rotation
        self.screens = ['system', 'network', 'qr']
        self.screen_idx = 0
        
        # MQTT (Explicit use of API v1 for compatibility)
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        if Config.MQTT_USER:
            self.client.username_pw_set(Config.MQTT_USER, Config.MQTT_PASSWORD)

    def start(self):
        """Start the service"""
        logger.info("Starting Display Controller...")
        logger.info(
            f"Display: type={Config.TYPE} size={Config.WIDTH}x{Config.HEIGHT} I2C port={Config.I2C_PORT} address=0x{Config.I2C_ADDRESS:02X}"
        )
        logger.info(
            f"MQTT: broker={Config.MQTT_BROKER}:{Config.MQTT_PORT} user={Config.MQTT_USER or '(none)'}"
        )
        logger.info(f"MQTT topics: {Config.MQTT_TOPICS}")
        logger.info(
            f"Initial mode={self.mode}, refresh={self.refresh}s, default_brightness={Config.DEFAULT_BRIGHTNESS}, QR_link={Config.QR_LINK}"
        )
        
        # Separate MQTT thread to avoid blocking if broker is down
        threading.Thread(target=self._mqtt_loop, daemon=True).start()

        # Main display loop
        while self.running:
            self._update_display()
            time.sleep(self.refresh)

    def stop(self, signum=None, frame=None):
        """Clean shutdown"""
        logger.info(f"Shutdown requested (Signal: {signum})")
        self.running = False
        self.oled.clear()
        sys.exit(0)

    def _mqtt_loop(self):
        """Manage MQTT connection and infinite reconnection attempts"""
        while self.running:
            try:
                logger.info(f"Connecting to MQTT broker {Config.MQTT_BROKER}...")
                self.client.connect(Config.MQTT_BROKER, Config.MQTT_PORT, 60)
                self.client.loop_forever() # Blocking while connected
            except Exception as e:
                logger.warning(f"MQTT error: {e}. Retrying in 10s...")
                time.sleep(10)

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info("MQTT connected")
            for topic in Config.MQTT_TOPICS.values():
                client.subscribe(topic)
            logger.info(f"Subscribed to topics: {list(Config.MQTT_TOPICS.values())}")
        else:
            logger.error(f"MQTT connection refused, code: {rc}")

    def _on_message(self, client, userdata, msg):
        try:
            topic = msg.topic
            payload = msg.payload.decode('utf-8')
            t = Config.MQTT_TOPICS

            if topic == t['text']:
                self.mode = "manual"
                self.text_buffer = payload
                logger.info(f"Received text message ({len(payload)} chars); switching to manual mode")
                self._update_display() # Rafraîchissement immédiat
            
            elif topic == t['mode']:
                if payload in ['auto', 'manual', 'system', 'network', 'qr', 'off']:
                    logger.info(f"Switching display mode to '{payload}'")
                    self.mode = payload
                    self._update_display()

            elif topic == t['command']:
                if payload == 'clear':
                    logger.info("Command received: clear display")
                    self.oled.clear()
                elif payload == 'on':
                    logger.info("Command received: power ON")
                    self.oled.power(True)
                elif payload == 'off':
                    logger.info("Command received: power OFF")
                    self.oled.power(False)

            elif topic == t['brightness']:
                try:
                    val = int(payload)
                    logger.info(f"Setting brightness/contrast to {val}")
                    self.oled.set_contrast(val)
                except ValueError:
                    logger.warning(f"Invalid brightness payload: '{payload}'")

            elif topic == t['refresh']:
                try:
                    self.refresh = int(payload)
                    logger.info(f"Setting refresh interval to {self.refresh}s")
                except ValueError:
                    logger.warning(f"Invalid refresh payload: '{payload}'")

        except Exception as e:
            logger.error(f"MQTT command error: {e}")

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


# --- ENTRY POINT ---
if __name__ == "__main__":
    controller = DisplayController()
    
    # Capture shutdown signals (Stop HA, Ctrl+C)
    signal.signal(signal.SIGTERM, controller.stop)
    signal.signal(signal.SIGINT, controller.stop)
    
    try:
        controller.start()
    except Exception as e:
        logger.critical(f"Fatal crash: {e}")
        controller.stop()