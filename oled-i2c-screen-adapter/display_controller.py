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
import json
import paho.mqtt.client as mqtt
from datetime import datetime
from PIL import Image, ImageFont, ImageDraw

# --- CONFIGURATION & SETUP ---
try:
    # Attempt to import OLED drivers
    from luma.core.interface.serial import i2c
    from luma.core.render import canvas
    from luma.oled.device import ssd1306, sh1106
except ImportError:
    logging.basicConfig(level=logging.ERROR)
    logging.critical("Critical error: Missing luma.oled libraries.")
    sys.exit(1)

class Config:
    # --- MQTT Configuration ---
    MQTT_BROKER = os.getenv('MQTT_BROKER', 'core-mqtt')
    MQTT_PORT = int(os.getenv('MQTT_PORT', 1883))
    MQTT_USER = os.getenv('MQTT_USER', 'homeassistant')
    MQTT_PASSWORD = os.getenv('MQTT_PASSWORD', '')
    
    # MQTT Topics definition
    MQTT_TOPICS = {
        'text': os.getenv('MQTT_TOPIC_TEXT', 'screen/text'),
        'mode': os.getenv('MQTT_TOPIC_MODE', 'screen/mode'),
        'brightness': os.getenv('MQTT_TOPIC_BRIGHTNESS', 'screen/brightness'),
        'status': os.getenv('MQTT_TOPIC_STATUS', 'screen/status'),
    }

    # --- Display & Logic Configuration ---
    I2C_ADDRESS = int(os.getenv('I2C_ADDRESS', '0x3C'), 16)
    I2C_PORT = int(os.getenv('I2C_PORT', 1))
    WIDTH = int(os.getenv('DISPLAY_WIDTH', 128))
    HEIGHT = int(os.getenv('DISPLAY_HEIGHT', 64))
    TYPE = os.getenv('DISPLAY_TYPE', 'ssd1306')
    
    FONT_SIZE = int(os.getenv('FONT_SIZE', 10))
    SHOW_HEADER = os.getenv('SHOW_HEADER', 'true').lower() == 'true'
    
    # Load custom lines configuration from JSON environment variable
    try:
        CUSTOM_LINES = json.loads(os.getenv('CUSTOM_LINES', '[]'))
    except:
        CUSTOM_LINES = []

    # Refresh rates and caching timers
    REFRESH_INTERVAL = int(os.getenv('REFRESH_INTERVAL', 5))
    SYSTEM_CACHE_INTERVAL = float(os.getenv('SYSTEM_CACHE_INTERVAL', 2.0))
    NETWORK_CACHE_INTERVAL = float(os.getenv('NETWORK_CACHE_INTERVAL', 3.0))
    DEFAULT_BRIGHTNESS = int(os.getenv('DEFAULT_BRIGHTNESS', 255))
    QR_LINK = os.getenv('QR_LINK', 'http://homeassistant.local:8123/')
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()

# --- Logging Setup ---
logging.basicConfig(level=getattr(logging, Config.LOG_LEVEL, logging.INFO), format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- HELPERS ---
class SystemMonitor:
    def __init__(self):
        # Initialize CPU monitoring
        try:
            psutil.cpu_percent(interval=None)
        except Exception as e:
            logger.error(f"CPU init error: {e}")
        
        # Initialize Cache variables
        self._system_cache = None
        self._system_cache_time = 0
        self._network_cache = None
        self._network_cache_time = 0
    
    def get_system_info(self, use_cache=True):
        """Retrieves system stats (CPU, RAM, Temp, Disk)."""
        now = time.time()
        
        # Return cache if valid
        if use_cache and self._system_cache and (now - self._system_cache_time) < Config.SYSTEM_CACHE_INTERVAL:
            return self._system_cache
        
        try:
            temp = 0
            # Try reading temperature from Linux thermal zone (Raspberry Pi specific)
            try:
                with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                    temp = float(f.read()) / 1000.0
            except:
                pass
            
            info = {
                'cpu': psutil.cpu_percent(interval=None),
                'ram': psutil.virtual_memory().percent,
                'temp': temp,
                'disk': psutil.disk_usage('/').percent
            }
            
            # Update cache
            self._system_cache = info
            self._system_cache_time = now
            
            return info
        except Exception as e:
            logger.error(f"SystemMonitor error: {e}")
            return self._system_cache or {'cpu': 0, 'ram': 0, 'temp': 0, 'disk': 0}

    def get_network_info(self, use_cache=True):
        """Retrieves network stats (IP, Online status, Ping)."""
        now = time.time()
        
        # Return cache if valid
        if use_cache and self._network_cache and (now - self._network_cache_time) < Config.NETWORK_CACHE_INTERVAL:
            return self._network_cache
        
        info = {'ip': 'N/A', 'online': False, 'ping': None, 'ifaces': {}}
        try:
            # 1. Detect Local IP via UDP socket (doesn't actually connect)
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(0.5)
                s.connect(("8.8.8.8", 80))
                info['ip'] = s.getsockname()[0]
                s.close()
            except Exception as e:
                logger.debug(f"Socket IP detection failed: {e}")

            # 2. List active network interfaces
            try:
                for iface in netifaces.interfaces():
                    if iface.startswith(('eth', 'wlan', 'en', 'wl')):
                        addrs = netifaces.ifaddresses(iface)
                        if netifaces.AF_INET in addrs:
                            info['ifaces'][iface] = addrs[netifaces.AF_INET][0]['addr']
            except Exception as e:
                logger.debug(f"Interface detection failed: {e}")

            # 3. Check connectivity via Ping (non-blocking call)
            try:
                res = subprocess.run(['ping', '-c', '1', '-W', '1', '8.8.8.8'], 
                                capture_output=True, text=True, timeout=1.5)
                info['online'] = (res.returncode == 0)
                if info['online']:
                    for line in res.stdout.split('\n'):
                        if 'time=' in line:
                            time_part = line.split('time=')[1]
                            info['ping'] = float(time_part.split()[0])
                            break
            except Exception as e:
                logger.debug(f"Ping test failed: {e}")

            # Update cache
            self._network_cache = info
            self._network_cache_time = now
            
            return info
        except Exception as e:
            logger.error(f"NetworkMonitor error: {e}")
            return self._network_cache or info


# --- OPTIMIZED RENDERER ---
class ScreenRenderer:
    def __init__(self, width, height):
        self.w = width
        self.h = height
        self.monitor = SystemMonitor()
        self.qr_img = None
        self._qr_generated = False
        
        # Font Management
        try:
            self.font = ImageFont.truetype("/usr/share/fonts/ttf-dejavu/DejaVuSans.ttf", Config.FONT_SIZE)
            self.font_header = ImageFont.truetype("/usr/share/fonts/ttf-dejavu/DejaVuSans-Bold.ttf", 10)
        except Exception as e:
            logger.warning(f"Font loading failed, using default: {e}")
            self.font = ImageFont.load_default()
            self.font_header = ImageFont.load_default()

        # Calculate dynamic line height
        try:
            bbox = self.font.getbbox("Ay")
            self.line_height = (bbox[3] - bbox[1]) + 2
        except:
            self.line_height = Config.FONT_SIZE + 2

    def _generate_qr(self):
        """Generates the QR code once and caches it."""
        if self._qr_generated:
            return self.qr_img
            
        try:
            qr = qrcode.QRCode(box_size=2, border=1)
            qr.add_data(Config.QR_LINK)
            qr.make(fit=True)
            img = qr.make_image(fill_color="white", back_color="black").convert('1')
            
            # Scale to fit screen
            scale = min(self.w / img.width, self.h / img.height)
            if scale != 1:
                img = img.resize((int(img.width * scale), int(img.height * scale)), Image.NEAREST)
            self.qr_img = img
            self._qr_generated = True
            return img
        except Exception as e:
            logger.error(f"QR generation failed: {e}")
            self._qr_generated = True
            return None

    def _header(self, draw, text):
        """Draws the top header bar."""
        if not Config.SHOW_HEADER:
            return 0
        h = 12
        draw.rectangle((0, 0, self.w, h), fill="white")
        draw.text((2, -1), text, fill="black", font=self.font_header)
        return h + 2 

    def _get_text_width(self, text):
        """Calculates text width in pixels."""
        try:
            return self.font.getlength(text)
        except AttributeError:
            return self.font.getsize(text)[0]
    
    def _truncate_text(self, text, max_width):
        """Truncates text to fit within max_width with an ellipsis."""
        if self._get_text_width(text) <= max_width:
            return text
        
        while len(text) > 0 and self._get_text_width(text + "...") > max_width:
            text = text[:-1]
        return text + "..." if text else ""

    # --- RENDERERS ---
    def render_system(self, draw):
        """Renders System Stats screen."""
        y = self._header(draw, "SYSTEM")
        info = self.monitor.get_system_info(use_cache=True)
        lines = [
            f"CPU: {info.get('cpu', 0):.0f}%  RAM: {info.get('ram', 0):.0f}%",
            f"Temp: {info.get('temp', 0):.1f}C",
            f"Disk: {info.get('disk', 0):.0f}%",
            f"Up: {datetime.now().strftime('%H:%M')}"
        ]
        for line in lines:
            if y + self.line_height > self.h:
                break
            draw.text((0, y), line, fill="white", font=self.font)
            y += self.line_height

    def render_qr(self, draw):
        """Renders the cached QR code."""
        qr_img = self._generate_qr()
        if qr_img:
            # Center the image
            x = (self.w - qr_img.width) // 2
            y = (self.h - qr_img.height) // 2
            draw.bitmap((x, y), qr_img, fill="white")
        else:
            draw.text((0, 20), "QR Error", fill="white", font=self.font)

    def render_text(self, draw, text, scroll_y=0):
        """Renders arbitrary text with vertical scrolling support."""
        header_h = self._header(draw, "MESSAGE")
        lines = text.split('\n')
        
        y = header_h - scroll_y
        
        for line in lines:
            if y >= self.h:
                break
            if y + self.line_height > header_h:
                truncated = self._truncate_text(line, self.w)
                draw.text((0, y), truncated, fill="white", font=self.font)
            y += self.line_height
    
    def get_text_content_height(self, text):
        """Calculates total height of text content."""
        lines = text.split('\n')
        return len(lines) * self.line_height

    def render_network(self, draw):
        """Renders Network Info screen."""
        info = self.monitor.get_network_info(use_cache=True)
        if not info:
            draw.text((0, 20), "Network Error", fill="white", font=self.font)
            return
            
        y = self._header(draw, "NETWORK")
        
        # Main IP
        if y + self.line_height <= self.h:
            draw.text((0, y), f"IP: {info['ip']}", fill="white", font=self.font)
            y += self.line_height
        
        # Interface list
        for iface, addr in list(info['ifaces'].items())[:3]:  # Limit to 3 interfaces
            if iface != 'lo' and y + self.line_height <= self.h:
                draw.text((0, y), f"{iface[:4]}: {addr}", fill="white", font=self.font)
                y += self.line_height
        
        # Network Status
        if y + self.line_height <= self.h:
            stat = "OK" if info['online'] else "DOWN"
            ping = f" {info['ping']:.0f}ms" if info['ping'] else ""
            draw.text((0, y), f"Net: {stat}{ping}", fill="white", font=self.font)

    def render_custom(self, draw, data_store, scroll_y=0):
        """Optimized custom data renderer with Vertical Scroll + Horizontal Marquee."""
        header_h = self._header(draw, "CUSTOM")
        current_y = header_h - scroll_y
        
        now = time.time()
        scroll_speed = 30
        gap = 40
        
        for item in Config.CUSTOM_LINES:
            fmt = item.get('format', '{}')
            topic = item.get('topic')
            val = data_store.get(topic, "...")
            
            try:
                text = fmt.format(val)
            except Exception as e:
                text = str(val)
            
            # Only draw if within visible vertical area
            if -self.line_height < current_y < self.h:
                w = self._get_text_width(text)
                
                if w <= self.w:
                    # Short text: static draw
                    draw.text((0, current_y), text, fill="white", font=self.font)
                else:
                    # Long text: Marquee animation
                    total_len = w + gap
                    x_offset = int(now * scroll_speed) % int(total_len)
                    x = -x_offset
                    
                    # Draw first instance
                    draw.text((x, current_y), text, fill="white", font=self.font)
                    
                    # Draw second instance for continuous effect
                    if x + w < self.w:
                        draw.text((x + total_len, current_y), text, fill="white", font=self.font)

            current_y += self.line_height

    def get_custom_content_height(self):
        """Returns total height of custom lines."""
        return len(Config.CUSTOM_LINES) * self.line_height


# --- OPTIMIZED MAIN CONTROLLER ---
class DisplayController:
    def __init__(self):
        self.running = True
        self.mqtt_connected = False
        
        # Initialize OLED Device
        try:
            serial = i2c(port=Config.I2C_PORT, address=Config.I2C_ADDRESS)
            if Config.TYPE == 'sh1106':
                self.oled = sh1106(serial, width=Config.WIDTH, height=Config.HEIGHT)
            else:
                self.oled = ssd1306(serial, width=Config.WIDTH, height=Config.HEIGHT)
        except Exception as e:
            logger.critical(f"OLED initialization failed: {e}")
            sys.exit(1)
            
        self.renderer = ScreenRenderer(Config.WIDTH, Config.HEIGHT)
        self.mode = "auto"
        self.previous_mode = "auto"
        self.screens = ['system', 'network', 'custom', 'qr']
        self.screen_idx = 0
        self.custom_data = {}
        self.current_text = ""
        self.brightness = Config.DEFAULT_BRIGHTNESS
        self.screen_on = True
        
        # Apply default brightness
        self._set_brightness(self.brightness)
        
        # Scroll States Initialization
        self.scroll_y = 0
        self.scroll_direction = 1 
        self.scroll_wait = 0
        self.text_scroll_y = 0
        self.text_scroll_direction = 1
        self.text_scroll_wait = 0

        # MQTT Client Setup
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect
        if Config.MQTT_USER:
            self.client.username_pw_set(Config.MQTT_USER, Config.MQTT_PASSWORD)
        
        # Watchdog for MQTT thread
        self.mqtt_thread = None
        self.mqtt_should_run = True

    def _set_brightness(self, value):
        """Sets OLED contrast (brightness) 0-255."""
        try:
            brightness = max(0, min(255, int(value)))
            self.oled.contrast(brightness)
            self.brightness = brightness
            logger.info(f"Brightness set to {brightness}")
            self._publish_status()
        except Exception as e:
            logger.error(f"Failed to set brightness: {e}")
    
    def _set_screen_state(self, on):
        """Physically turns screen ON or OFF."""
        try:
            if on:
                self.oled.show()
                self.screen_on = True
                logger.info("Screen turned ON")
            else:
                self.oled.hide()
                self.screen_on = False
                logger.info("Screen turned OFF")
            self._publish_status()
        except Exception as e:
            logger.error(f"Failed to change screen state: {e}")

    def _publish_status(self):
        """Publishes current screen state to MQTT."""
        if self.mqtt_connected:
            try:
                status = {
                    'mode': self.mode,
                    'brightness': self.brightness,
                    'screen_on': self.screen_on,
                    'current_screen': self.screens[self.screen_idx] if self.mode == 'auto' else self.mode
                }
                self.client.publish(Config.MQTT_TOPICS['status'], json.dumps(status), retain=True)
            except Exception as e:
                logger.error(f"Failed to publish status: {e}")

    def start(self):
        """Main application loop."""
        logger.info(f"Display started. Size: {Config.WIDTH}x{Config.HEIGHT}, Font: {Config.FONT_SIZE}px, Brightness: {self.brightness}")
        
        # Start MQTT in a separate thread
        self._start_mqtt_thread()

        last_screen_change = time.time()
        
        while self.running:
            try:
                loop_start = time.time()

                # Watchdog: Restart MQTT thread if it died
                if self.mqtt_thread and not self.mqtt_thread.is_alive():
                    logger.warning("MQTT thread died, restarting...")
                    self._start_mqtt_thread()

                # Handle Auto Rotation Mode
                if self.mode == 'auto':
                    if loop_start - last_screen_change > Config.REFRESH_INTERVAL:
                        self.screen_idx = (self.screen_idx + 1) % len(self.screens)
                        last_screen_change = loop_start
                        self.scroll_y = 0 
                        self.scroll_direction = 1
                        self.scroll_wait = 20
                        self._publish_status()

                current_screen = self.screens[self.screen_idx] if self.mode == 'auto' else self.mode

                # Handle Vertical Scrolling logic
                if current_screen == 'custom':
                    self._handle_vertical_scroll()
                elif current_screen == 'text':
                    self._handle_text_scroll()
                
                # Render the frame
                self._draw_frame(current_screen)

                # Control Framerate (~15 FPS)
                time.sleep(0.06)
                
            except Exception as e:
                logger.error(f"Main loop error: {e}", exc_info=True)
                time.sleep(1)

    def _start_mqtt_thread(self):
        """Starts the MQTT client loop safely."""
        self.mqtt_should_run = True
        self.mqtt_thread = threading.Thread(target=self._mqtt_loop, daemon=True)
        self.mqtt_thread.start()

    def _handle_vertical_scroll(self):
        """Calculates vertical scrolling positions for Custom mode."""
        total_h = self.renderer.get_custom_content_height()
        header_h = 14 if Config.SHOW_HEADER else 0
        visible_h = Config.HEIGHT - header_h
        
        if total_h <= visible_h:
            self.scroll_y = 0
            return

        # Pause at top/bottom
        if self.scroll_wait > 0:
            self.scroll_wait -= 1
            return

        self.scroll_y += self.scroll_direction

        # Reverse direction when hitting limits
        max_scroll = total_h - visible_h
        if self.scroll_y >= max_scroll:
            self.scroll_y = max_scroll
            self.scroll_direction = -1
            self.scroll_wait = 30
        elif self.scroll_y <= 0:
            self.scroll_y = 0
            self.scroll_direction = 1
            self.scroll_wait = 30
    
    def _handle_text_scroll(self):
        """Calculates vertical scrolling positions for Text mode."""
        total_h = self.renderer.get_text_content_height(self.current_text)
        header_h = 14 if Config.SHOW_HEADER else 0
        visible_h = Config.HEIGHT - header_h
        
        if total_h <= visible_h:
            self.text_scroll_y = 0
            return

        if self.text_scroll_wait > 0:
            self.text_scroll_wait -= 1
            return

        self.text_scroll_y += self.text_scroll_direction

        max_scroll = total_h - visible_h
        if self.text_scroll_y >= max_scroll:
            self.text_scroll_y = max_scroll
            self.text_scroll_direction = -1
            self.text_scroll_wait = 30
        elif self.text_scroll_y <= 0:
            self.text_scroll_y = 0
            self.text_scroll_direction = 1
            self.text_scroll_wait = 30

    def _draw_frame(self, screen_name):
        """Directs rendering to the appropriate method."""
        try:
            # Handle 'off' mode physically
            if screen_name == 'off':
                if self.screen_on:
                    self._set_screen_state(False)
                return
            else:
                if not self.screen_on:
                    self._set_screen_state(True)
            
            with canvas(self.oled) as draw:
                if screen_name == 'system':
                    self.renderer.render_system(draw)
                elif screen_name == 'qr':
                    self.renderer.render_qr(draw)
                elif screen_name == 'custom':
                    self.renderer.render_custom(draw, self.custom_data, self.scroll_y)
                elif screen_name == 'network':
                    self.renderer.render_network(draw)
                elif screen_name == 'text':
                    self.renderer.render_text(draw, self.current_text, self.text_scroll_y)
                    
        except Exception as e:
            logger.error(f"Draw frame error: {e}")

    def stop(self, *args):
        """Gracefully stops the controller."""
        logger.info("Stopping display controller...")
        self.running = False
        self.mqtt_should_run = False
        try:
            self.client.disconnect()
        except:
            pass
        try:
            self.oled.hide()
        except:
            pass
        sys.exit(0)

    def _mqtt_loop(self):
        """Blocking MQTT loop with auto-reconnect logic."""
        while self.running and self.mqtt_should_run:
            try:
                logger.info(f"Connecting to MQTT broker {Config.MQTT_BROKER}:{Config.MQTT_PORT}...")
                self.client.connect(Config.MQTT_BROKER, Config.MQTT_PORT, 60)
                self.client.loop_forever()
            except Exception as e:
                logger.error(f"MQTT connection error: {e}")
                self.mqtt_connected = False
                time.sleep(5)

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info("MQTT connected successfully")
            self.mqtt_connected = True
            
            # Subscribe to main control topics
            for topic in Config.MQTT_TOPICS.values():
                if topic != Config.MQTT_TOPICS['status']:  # Do not subscribe to status topic (loop risk)
                    client.subscribe(topic)
                    
            # Subscribe to dynamic custom line topics
            for line in Config.CUSTOM_LINES:
                if line.get('topic'):
                    client.subscribe(line.get('topic'))
            
            # Publish initial status
            self._publish_status()
        else:
            logger.error(f"MQTT connection failed with code {rc}")
            self.mqtt_connected = False
    
    def _on_disconnect(self, client, userdata, rc):
        logger.warning(f"MQTT disconnected with code {rc}")
        self.mqtt_connected = False

    def _on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode()
            
            # Handle Mode Change
            if msg.topic == Config.MQTT_TOPICS['mode']:
                old_mode = self.mode
                self.mode = payload
                
                # Reset scrolling when mode changes
                if old_mode != self.mode:
                    self.scroll_y = 0
                    self.scroll_direction = 1
                    self.scroll_wait = 20
                    self.text_scroll_y = 0
                    self.text_scroll_direction = 1
                    self.text_scroll_wait = 20
                    logger.info(f"Mode changed: {old_mode} -> {payload}")
                    self._publish_status()
                
            # Handle Text Content Update
            elif msg.topic == Config.MQTT_TOPICS['text']:
                self.current_text = payload
                self.text_scroll_y = 0
                self.text_scroll_direction = 1
                self.text_scroll_wait = 20
                
            # Handle Brightness Update
            elif msg.topic == Config.MQTT_TOPICS['brightness']:
                try:
                    brightness_value = int(payload)
                    self._set_brightness(brightness_value)
                except ValueError:
                    logger.error(f"Invalid brightness value: {payload}")
                
            # Update Custom Data Store
            self.custom_data[msg.topic] = payload
            
        except Exception as e:
            logger.error(f"Message handling error: {e}")


if __name__ == "__main__":
    c = DisplayController()
    # Handle system signals for graceful exit
    signal.signal(signal.SIGTERM, c.stop)
    signal.signal(signal.SIGINT, c.stop)
    try:
        c.start()
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)