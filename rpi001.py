import time
import json
import threading
import urllib.parse
import urllib.request

import paho.mqtt.client as mqtt
import telepot
from telepot.namedtuple import ReplyKeyboardMarkup, KeyboardButton
import BlynkLib

# ============================================================
# CONFIGURATION
# ============================================================

MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883
MQTT_KEEPALIVE = 60

# Rpi002 -> Rpi001
TOPIC_TEMP = "home/rpi002/tmp"
TOPIC_HUMI = "home/rpi002/humi"
TOPIC_LIGHT = "home/rpi002/light"
TOPIC_MOTION = "home/rpi002/motion"
TOPIC_STATUS = "home/rpi002/status"

# Rpi001 -> Rpi002
TOPIC_LED_COMMAND = "home/rpi001/led"
TOPIC_RPI001_STATUS = "home/rpi001/status"

# Blynk virtual pins:
# V0 = temperature
# V1 = humidity
# V2 = light intensity
# V3 = motion
# V4 = temperature alert, 1 if temperature > temp threshold
# V5 = light alert, 1 if light intensity < light threshold
# V6 = Telegram LED control, 1 when Telegram LED command is ON
BLYNK_AUTH_TOKEN = "ssrA7_Ss-i4TVMnaao9XOC17Y8Bz7zIo"

TELEGRAM_BOT_TOKEN = "8680795684:AAHK5P8o3gwObrmCKAbVWjrzQe4YPa57-sE"
TELEGRAM_CHAT_ID = "8659920598"

MOTION_ALERT_COOLDOWN = 5

# Rpi002 publishes sensor data every 3 seconds and status every 10 seconds.
RPI002_TIMEOUT = 5

# ============================================================
# GLOBAL VARIABLES
# ============================================================

start_time = time.time()

blynk = None
blynk_connected = False

bot = None
telegram_offset = None

mqtt_client = None
mqtt_connected = False

program_running = True
last_motion_alert_time = 0
last_rpi002_seen = None

sensor_data = {
    "temperature": None,
    "humidity": None,
    "light": None,
    "motion": 0,
    "status": "OFFLINE",
    "temp_threshold": None,
    "light_threshold": None,
    "telegram_led": None,
}

# ============================================================
# BASIC HELPER FUNCTIONS
# ============================================================

def log(label, message):
    now = int(time.time() - start_time)
    print(f"[{now}s] [{label}] {message}")


def format_value(value, unit=""):
    if value is None:
        return "N/A"

    if isinstance(value, float):
        value = f"{value:.1f}"

    if unit == "":
        return str(value)

    return str(value) + " " + unit


def clear_sensor_values():
    sensor_data["temperature"] = None
    sensor_data["humidity"] = None
    sensor_data["light"] = None
    sensor_data["motion"] = 0
    sensor_data["temp_threshold"] = None
    sensor_data["light_threshold"] = None
    sensor_data["telegram_led"] = None


def set_rpi002_status(new_status):
    old_status = sensor_data["status"]

    if old_status == new_status:
        return

    sensor_data["status"] = new_status

    if new_status == "ONLINE":
        log("RPI002", "Rpi002 is ONLINE")
        send_telegram("Rpi002 is ONLINE", reply_markup=get_keyboard())

    elif new_status == "OFFLINE":
        log("RPI002", "Rpi002 is OFFLINE")
        send_telegram("Rpi002 is OFFLINE", reply_markup=get_keyboard())


def mark_rpi002_online():
    global last_rpi002_seen

    last_rpi002_seen = time.time()
    set_rpi002_status("ONLINE")


def check_rpi002_timeout():
    if last_rpi002_seen is None:
        sensor_data["status"] = "OFFLINE"
        return

    elapsed = time.time() - last_rpi002_seen

    if elapsed > RPI002_TIMEOUT:
        clear_sensor_values()
        set_rpi002_status("OFFLINE")
        update_blynk()


def get_motion_text():
    if int(sensor_data["motion"]) == 1:
        return "Motion Detected"
    return "No Motion"


def get_led_text():
    if sensor_data["telegram_led"] is None:
        return "N/A"
    if sensor_data["telegram_led"]:
        return "ON"
    return "OFF"


def get_temp_alert():
    temp = sensor_data["temperature"]
    threshold = sensor_data["temp_threshold"]

    if temp is None or threshold is None:
        return 0

    if temp > threshold:
        return 1

    return 0


def get_light_alert():
    light = sensor_data["light"]
    threshold = sensor_data["light_threshold"]

    if light is None or threshold is None:
        return 0

    if light < threshold:
        return 1

    return 0


def get_telegram_led_assert():
    if sensor_data["telegram_led"] is None:
        return 0

    if sensor_data["telegram_led"]:
        return 1

    return 0


def get_status_message():
    status = sensor_data["status"]

    text = (
        "S.M.A.R.T Home Status\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Rpi002 Status: {status}\n"
    )

    if status != "ONLINE":
        text += "Rpi002 is OFFLINE. Sensor values are unavailable.\n"

    text += (
        "\nSensor Readings\n"
        f"Temperature: {format_value(sensor_data['temperature'], '°C')}\n"
        f"Humidity: {format_value(sensor_data['humidity'], '%')}\n"
        f"Light Intensity: {format_value(sensor_data['light'], '%')}\n"
        f"Motion: {get_motion_text()}\n\n"
        "Thresholds set at Rpi002 GUI\n"
        f"Temp Threshold: {format_value(sensor_data['temp_threshold'], '°C')}\n"
        f"Light Threshold: {format_value(sensor_data['light_threshold'], '%')}\n\n"
        f"Telegram LED: {get_led_text()}"
    )

    return text


def get_command_list_message():
    return (
        "Available Commands\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Temperature: /temp\n"
        "Humidity: /humidity\n"
        "Light Intensity: /intensity\n"
        "Motion: /motion\n"
        "Status: /status\n"
        "LED ON: /led_on\n"
        "LED OFF: /led_off"
    )


# ============================================================
# BLYNK FUNCTIONS
# ============================================================

def setup_blynk():
    global blynk, blynk_connected

    try:
        blynk = BlynkLib.Blynk(BLYNK_AUTH_TOKEN)

        @blynk.on("connected")
        def blynk_connected_handler(ping_ms=None):
            global blynk_connected
            blynk_connected = True
            log("BLYNK", "Connected")

        @blynk.on("disconnected")
        def blynk_disconnected_handler():
            global blynk_connected
            blynk_connected = False
            log("BLYNK", "Disconnected")

        log("BLYNK", "Setup completed")

    except Exception as e:
        log("BLYNK", "Setup error: " + str(e))


def update_blynk():
    if blynk is None or not blynk_connected:
        return

    try:
        temp_alert = get_temp_alert()
        light_alert = get_light_alert()
        telegram_led_assert = get_telegram_led_assert()

        blynk.virtual_write(0, sensor_data["temperature"] if sensor_data["temperature"] is not None else "N/A")
        blynk.virtual_write(1, sensor_data["humidity"] if sensor_data["humidity"] is not None else "N/A")
        blynk.virtual_write(2, sensor_data["light"] if sensor_data["light"] is not None else "N/A")
        blynk.virtual_write(3, sensor_data["motion"])

        # V4 asserts when temperature exceeds temperature threshold.
        blynk.virtual_write(4, temp_alert)

        # V5 asserts when light intensity is lower than light threshold.
        blynk.virtual_write(5, light_alert)

        # V6 asserts when Telegram LED command is ON.
        blynk.virtual_write(6, telegram_led_assert)

    except Exception as e:
        log("BLYNK", "Update error: " + str(e))


# ============================================================
# TELEGRAM FUNCTIONS
# ============================================================

def setup_telegram():
    global bot

    try:
        bot = telepot.Bot(TELEGRAM_BOT_TOKEN)
        setup_telegram_command_menu()
        log("TELEGRAM", "Bot setup completed")

    except Exception as e:
        log("TELEGRAM", "Setup error: " + str(e))


def setup_telegram_command_menu():
    try:
        # These commands appear when the user types "/" in Telegram.
        # Telegram displays them as: /temp - Temperature
        commands = [
            {"command": "temp", "description": "Temperature"},
            {"command": "humidity", "description": "Humidity"},
            {"command": "intensity", "description": "Light Intensity"},
            {"command": "motion", "description": "Motion"},
            {"command": "status", "description": "System Status"},
            {"command": "led_on", "description": "Turn ON Telegram LED"},
            {"command": "led_off", "description": "Turn OFF Telegram LED"},
            {"command": "help", "description": "Show command list"},
        ]

        api_url = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/setMyCommands"
        post_data = urllib.parse.urlencode({
            "commands": json.dumps(commands)
        }).encode("utf-8")

        urllib.request.urlopen(api_url, data=post_data, timeout=10)
        log("TELEGRAM", "Command menu updated")

    except Exception as e:
        log("TELEGRAM", "Command menu setup error: " + str(e))


def get_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Status")],
            [KeyboardButton(text="Temperature"), KeyboardButton(text="Humidity")],
            [KeyboardButton(text="Intensity"), KeyboardButton(text="Motion")],
            [KeyboardButton(text="LED ON"), KeyboardButton(text="LED OFF")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False
    )


def send_telegram(text, chat_id=None, reply_markup=None):
    if bot is None:
        return

    try:
        if chat_id is None:
            chat_id = TELEGRAM_CHAT_ID

        if reply_markup is None:
            bot.sendMessage(chat_id, text)
        else:
            bot.sendMessage(chat_id, text, reply_markup=reply_markup)

        log("TELEGRAM", "Message sent: " + text.split("\n")[0])

    except Exception as e:
        log("TELEGRAM", "Send error: " + str(e))


def telegram_loop():
    global telegram_offset

    log("TELEGRAM", "Thread started")

    while program_running:
        if bot is None:
            time.sleep(1)
            continue

        try:
            updates = bot.getUpdates(offset=telegram_offset, timeout=10)

            for update in updates:
                telegram_offset = update["update_id"] + 1

                if "message" in update:
                    handle_telegram_message(update["message"])

        except Exception as e:
            log("TELEGRAM", "Checking error: " + str(e))
            time.sleep(2)


def handle_telegram_message(message):
    try:
        content_type, chat_type, chat_id = telepot.glance(message)

        if content_type != "text":
            send_telegram("Please send text command only.", chat_id, get_keyboard())
            return

        if str(chat_id) != str(TELEGRAM_CHAT_ID):
            send_telegram("Unauthorized user.", chat_id)
            return

        command = message["text"].strip()
        log("TELEGRAM", "Command received: " + command)
        handle_telegram_command(chat_id, command)

    except Exception as e:
        log("TELEGRAM", "Message error: " + str(e))


def normalize_command(command):
    command = command.lower().strip()

    # Button text mapping
    if command in ["status", "status (/status)"]:
        return "/status"
    if command in ["temperature", "temperature (/temp)"]:
        return "/temp"
    if command in ["humidity", "humidity (/humidity)", "humidity (/humi)"]:
        return "/humidity"
    if command in ["intensity", "light", "intensity (/intensity)", "intensity (/light)"]:
        return "/intensity"
    if command in ["motion", "motion (/motion)"]:
        return "/motion"
    if command in ["led on", "led on (/led_on)"]:
        return "/led_on"
    if command in ["led off", "led off (/led_off)"]:
        return "/led_off"

    return command


def handle_telegram_command(chat_id, command):
    command = normalize_command(command)

    if command == "/help":
        send_telegram(get_command_list_message(), chat_id)

    elif command == "/status":
        send_telegram(get_status_message(), chat_id)

    elif command == "/temp":
        send_telegram("Temperature: " + format_value(sensor_data["temperature"], "°C"), chat_id)

    elif command == "/humidity":
        send_telegram("Humidity: " + format_value(sensor_data["humidity"], "%"), chat_id)

    elif command == "/intensity":
        send_telegram("Light Intensity: " + format_value(sensor_data["light"], "%"), chat_id)

    elif command == "/motion":
        send_telegram("Motion: " + get_motion_text(), chat_id)

    elif command == "/led_on":
        publish_led_command("ON")
        send_telegram("LED command sent to Rpi002: ON", chat_id)

    elif command == "/led_off":
        publish_led_command("OFF")
        send_telegram("LED command sent to Rpi002: OFF", chat_id)

    else:
        send_telegram(
            "Unknown command.\nType / to choose a command, tap a button, or use /help.",
            chat_id,
            get_keyboard()
        )


# ============================================================
# MQTT FUNCTIONS
# ============================================================

def create_mqtt_client():
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    except Exception:
        return mqtt.Client()


def setup_mqtt():
    global mqtt_client

    mqtt_client = create_mqtt_client()
    mqtt_client.on_connect = on_mqtt_connect
    mqtt_client.on_disconnect = on_mqtt_disconnect
    mqtt_client.on_message = on_mqtt_message

    mqtt_client.reconnect_delay_set(min_delay=1, max_delay=10)

    # If Rpi001 shuts down suddenly, Mosquitto will publish OFFLINE.
    mqtt_client.will_set(TOPIC_RPI001_STATUS, "offline", qos=1, retain=True)

    try:
        mqtt_client.connect_async(MQTT_BROKER, MQTT_PORT, MQTT_KEEPALIVE)
        mqtt_client.loop_start()
        log("MQTT", "Setup completed")

    except Exception as e:
        log("MQTT", "Setup error: " + str(e))


def on_mqtt_connect(client, userdata, flags, rc):
    global mqtt_connected

    if rc == 0:
        mqtt_connected = True
        log("MQTT", "Connected to broker")

        client.subscribe("home/rpi002/#", qos=1)
        log("MQTT", "Subscribed: home/rpi002/#")

        publish_rpi001_status("online")

    else:
        mqtt_connected = False
        log("MQTT", "Connection failed: " + str(rc))


def on_mqtt_disconnect(client, userdata, rc):
    global mqtt_connected

    mqtt_connected = False
    log("MQTT", "Disconnected from broker, rc=" + str(rc))


def publish_rpi001_status(status):
    if mqtt_client is None:
        return

    try:
        mqtt_client.publish(TOPIC_RPI001_STATUS, status, qos=1, retain=True)
        log("MQTT PUBLISHED", TOPIC_RPI001_STATUS + " = " + status)

    except Exception as e:
        log("MQTT", "Rpi001 status publish error: " + str(e))


def parse_status_json(payload):
    try:
        data = json.loads(payload)

        if data.get("temperature") is not None:
            sensor_data["temperature"] = float(data["temperature"])
        if data.get("humidity") is not None:
            sensor_data["humidity"] = float(data["humidity"])
        if data.get("light") is not None:
            sensor_data["light"] = float(data["light"])
        if data.get("motion") is not None:
            sensor_data["motion"] = int(data["motion"])
        if data.get("temp_threshold") is not None:
            sensor_data["temp_threshold"] = float(data["temp_threshold"])
        if data.get("light_threshold") is not None:
            sensor_data["light_threshold"] = float(data["light_threshold"])
        if data.get("green_blink") is not None:
            sensor_data["telegram_led"] = bool(data["green_blink"])

    except json.JSONDecodeError:
        if payload.lower() == "online":
            mark_rpi002_online()
        else:
            sensor_data["status"] = payload.upper()

    except Exception as e:
        log("MQTT", "Status JSON parse error: " + str(e))


def on_mqtt_message(client, userdata, message):
    global last_motion_alert_time

    try:
        topic = message.topic
        payload = message.payload.decode().strip()

        log("MQTT RECEIVED", topic + " = " + payload)
        mark_rpi002_online()

        if topic == TOPIC_TEMP:
            sensor_data["temperature"] = float(payload)

        elif topic == TOPIC_HUMI:
            sensor_data["humidity"] = float(payload)

        elif topic == TOPIC_LIGHT:
            sensor_data["light"] = float(payload)

        elif topic == TOPIC_MOTION:
            sensor_data["motion"] = int(payload)

            if int(payload) == 1:
                now = time.time()

                if now - last_motion_alert_time >= MOTION_ALERT_COOLDOWN:
                    send_telegram("Motion detected from Rpi002.", reply_markup=get_keyboard())
                    last_motion_alert_time = now

        elif topic == TOPIC_STATUS:
            parse_status_json(payload)

        update_blynk()

    except Exception as e:
        log("MQTT", "Message error: " + str(e))


def publish_led_command(command):
    if mqtt_client is None:
        log("MQTT", "Client not ready")
        return

    try:
        mqtt_client.publish(TOPIC_LED_COMMAND, command, qos=1)
        log("MQTT PUBLISHED", TOPIC_LED_COMMAND + " = " + command)

        # Update local Telegram LED state for Blynk V6 immediately.
        if command.upper() == "ON":
            sensor_data["telegram_led"] = True
        elif command.upper() == "OFF":
            sensor_data["telegram_led"] = False

        update_blynk()

    except Exception as e:
        log("MQTT", "LED publish error: " + str(e))


# ============================================================
# MAIN PROGRAM
# ============================================================

def main():
    global program_running

    log("SYSTEM", "Starting Rpi001 gateway")

    setup_blynk()
    setup_telegram()
    setup_mqtt()

    telegram_thread = threading.Thread(target=telegram_loop, daemon=True)
    telegram_thread.start()

    log("SYSTEM", "Rpi001 gateway is running")

    while program_running:
        try:
            check_rpi002_timeout()

            if blynk is not None:
                blynk.run()

            time.sleep(0.2)

        except KeyboardInterrupt:
            log("SYSTEM", "Stopping program")
            program_running = False

        except Exception as e:
            log("SYSTEM", "Main loop error: " + str(e))
            time.sleep(1)

    if mqtt_client is not None:
        try:
            publish_rpi001_status("offline")
            time.sleep(0.5)
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
        except Exception:
            pass

    log("SYSTEM", "Rpi001 gateway stopped")


if __name__ == "__main__":
    main()
