![HA Add-on](https://img.shields.io/badge/Home--Assistant-Add--on-blue?logo=home-assistant)

# Home Assistant Add-on: OLED I2C Screen Adapter

This Home Assistant add-on enables the use of an OLED screen via I2C on a Raspberry Pi running Home Assistant Operating System (HA OS).  
HA OS typically restricts direct access to I2C due to system permissions, but this add-on bypasses those limitations by leveraging the Supervisor.

## Features

- **I2C Screen Support**: Compatible with OLED screens using GME12864, SSD1306, or SH1106 controllers.
- **MQTT Integration**: Receives data via MQTT topics to display info.
- **Customizable Display Logic**: The `display_controller.py` script can be modified to define new display modes, MQTT topics, or behaviors.
- **Supervisor Execution**: Ensures compatibility with HA OS by executing with Supervisor permissions.
- **UI-Based Configuration**: Add-on options are configurable through the Home Assistant interface 

## Documentation
For detailed documentation, including installation instructions, configuration options, and usage examples, please refer to the [official documentation](https://github.com/Valeyrian/Home_Assistant_Add-on_OLED_I2C_Screen_Adapter/blob/main/oled-i2c-screen-adapter/DOCS.md).

## Contributing

Feel free to contribute by submitting issues or pull requests. You can:

- Add support for new OLED controllers
- Introduce new display modes or animations
- Improve MQTT topic parsing
- Refactor or extend `display_controller.py`

## License

This project is licensed under the MIT License. See the `LICENSE` file for more information.

## Acknowledgments

- Home Assistant for providing a powerful automation platform  
- Luma OLED for the screen rendering library  
- The Raspberry Pi community for tutorials and support

---
