# Installation

## Prerequisites

1. Ensure that I2C is enabled on your Raspberry Pi.  You must enable it manually by editing the `config.txt` file in the boot partition of your Raspberry Pi.:
    ```txt
    dtparam=i2c_arm=on
    dtparam=i2c1=on
    ```
    (This add-on does **not** enable I2C automatically.)

2. (Optionally) Access your Home Assistant instance via SSH or SFTP to customise the add-on.


## Installation Methods

### Method 1: Easy Installation via Home Assistant UI (Recommended)

1. In Home Assistant, navigate to **Settings > Add-ons > Add-on Store**.
2. Click the **three dots menu** (⋮) in the top-right corner.
3. Select **Repositories**.
4. Add this repository URL:
   ```
   https://github.com/Valeyrian/Home_Assistant_Add-on_OLED_I2C_Screen_Adapter
   ```
5. Click **Add** and wait for the repository to be loaded.
6. Find **OLED I2C Screen Adapter** in the add-on list and click on it.
7. Click **Install** and wait for installation to complete.
8. Configure the add-on via the **Configuration** tab.
9. Start the add-on.

### Method 2: Manual Installation

1. Clone or download this repository.
2. Copy the repository folder into a **subfolder** inside the addons directory of your Home Assistant instance. This subfolder must have the exact same name as the `slug` defined in `config.json`, using **only** lowercase letters, numbers, and hyphens (no capital letters or special characters).
3. Restart Home Assistant to detect the new add-on.
4. Navigate to **Settings > Add-ons** in Home Assistant and install the add-on.
5. Configure the add-on via the Home Assistant UI.

For advanced users, you can manually edit the `config.yaml` file inside the add-on folder to customize settings beyond the UI options. However, this is usually not necessary.

Refer to the official documentation for more info:  
[Home Assistant Add-ons](https://www.home-assistant.io/addons/)

---

# Configuration

## Add-on Options

All options are accessible through the Home Assistant UI:

| Option               | Description                                      | Default                    |
|----------------------|--------------------------------------------------|----------------------------|
| `mqtt_broker`        | MQTT broker address                              | `core-mqtt`                |
| `mqtt_port`          | MQTT broker port                                 | `1883`                     |
| `mqtt_user`          | MQTT username                                    | `homeassistant`            |
| `mqtt_password`      | MQTT password                                    | (empty)                    |
| `i2c_address`        | I2C display address                              | `0x3C`                     |
| `i2c_port`           | I2C port                                         | `1`                        |
| `display_width`      | Width in pixels                                  | `128`                      |
| `display_height`     | Height in pixels                                 | `64`                       |
| `display_type`       | Controller type (`ssd1306` or `sh1106`)          | `ssd1306`                  |
| `refresh_interval`   | Refresh time in seconds                          | `5`                        |
| `auto_brightness`    | Enable automatic brightness                      | `true`                     |
| `default_brightness` | Manual brightness level (0–255)                  | `255`                      |
| `qr_link`            | URL shown as QR code                             | `http://homeassistant.local:8123/` |


## MQTT Topics

The screen listens for MQTT messages on the following topics:

| Topic                         | Description                           |
|------------------------------|----------------------------------------|
| `screen/gme12864/text`       | Send text to display                   |
| `screen/gme12864/command`    | Send commands (`clear`, `on`, `off`)   |
| `screen/gme12864/mode`       | Change mode (`auto`, `manual`, etc.)   |
| `screen/gme12864/brightness` | Adjust brightness                      |
| `screen/gme12864/refresh`    | Change refresh interval                |

You can freely extend or change these topics by editing the `display_controller.py` file.

# Usage 
##   Display Modes

The add-on supports multiple display modes:

| Mode     | Description                                                              |
|----------|--------------------------------------------------------------------------|
| `auto`   | Automatically rotates through all screens                                |
| `manual` | Static custom message sent via MQTT                                      |
| `system` | Shows CPU usage, memory, uptime                                          |
| `network`| Displays IP and network status                                           |


##  Customization

You can customize the display logic by editing the `display_controller.py` script. This allows you to add new display modes, change MQTT topic handling, or build menu systems. 

> While the `config.yaml` file can also be edited manually Most configurations can be handled through the Home Assistant UI.

#  Warnings & Important Notes

- **The I2C screen must be connected before starting the add-on.**
- If you change MQTT topics or display modes manually in `display_controller.py`, make sure they match your MQTT publisher configuration.
- Incorrect I2C address or port may cause the screen to stay black or the add-on to crash silently.
- This add-on **requires Home Assistant OS with Supervisor access**. It won't work with Home Assistant Core or Container.
- You must configure **valid MQTT credentials** if your broker is protected (which it usually is).


---

#  Troubleshooting

### Screen doesn't turn on
- Check that I2C is enabled on the Pi (`dtparam=i2c_arm=on`).
- Make sure the screen is connected to the correct pins and powered.
- Double-check the I2C address (e.g., `0x3C`). Use `i2cdetect` to scan devices.

### MQTT messages not received
- Verify that the MQTT broker, username, and password are correctly set.
- Check the Home Assistant MQTT integration is active and functional.
- Use a tool like `MQTT Explorer` to debug your messages.

### Add-on not appearing in UI
- Make sure the folder name **exactly matches** the slug in `config.json`, using only lowercase and hyphens.
- Restart Home Assistant and verify the `config.json` file is valid.

### Add-on fails to start
- Check logs via **Supervisor > Add-ons > Logs**.
- Ensure the I2C device (e.g., `/dev/i2c-1`) is exposed and detected.

---

##  MQTT Examples

### Send text to screen
```json
Topic: screen/gme12864/text
Payload: "Hello from Home Assistant!"
```

### Change display mode
```json
Topic: screen/gme12864/mode
Payload: "auto"
```
---

# Dependencies

The following Python libraries are used by this add-on and are automatically installed:

- `paho-mqtt`
- `smbus`
- `smbus2`
- `Pillow`
- `luma.oled`
- `psutil`
- `netifaces`

---
# Limitations

- While designed for Raspberry Pi, this add-on may also work on other devices with I2C support.
- The add-on is primarily tested with SSD1306 and SH1106 controllers. Other controllers may require additional configuration or code changes.
- The add-on does not support dynamic screen resizing; the display size is fixed.

---

# License
This project is licensed under the MIT License. See the `LICENSE` file for more information.