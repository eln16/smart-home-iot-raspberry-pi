# ============================================================
#  RPI002 - SENSOR UNIT (OPTION A)
#  SMART Home System
#  ------------------------------------------------------------
#  Features:
#  - DHT22 : temperature + humidity
#  - MPU6050 : motion detection
#  - LDR + ADS1115 : light intensity (converted to percentage)
#  - GUI threshold setting (USER sets from Rpi002 only)
#  - RED LED blinks if temperature > threshold
#  - YELLOW LED blinks if light percentage < threshold
#  - GREEN LED blinks if MQTT command received from Rpi001
#  - Publishes sensor data to:
#       home/rpi002/tmp
#       home/rpi002/humi
#       home/rpi002/light
#       home/rpi002/motion
#       home/rpi002/status
# ============================================================

from tkinter import *
from tkinter import scrolledtext, messagebox
import time
import json
import schedule
from queue import Queue, Empty

import RPi.GPIO as GPIO
import board
import busio
import adafruit_dht
from mpu6050 import mpu6050
import paho.mqtt.client as mqtt

import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn

# ============================================================
#  MQTT CONFIG
# ============================================================
MQTT_BROKER = "10.10.7.73"     
MQTT_PORT = 1883
MQTT_CLIENT_ID = "Rpi002"

# Rpi002 publishes sensor data to these topics
TOPIC_TEMP = "home/rpi002/tmp"
TOPIC_HUMI = "home/rpi002/humi"
TOPIC_LIGHT = "home/rpi002/light"
TOPIC_MOTION = "home/rpi002/motion"
TOPIC_STATUS = "home/rpi002/status"

# Rpi002 subscribes to these topics
TOPIC_LED_COMMAND = "home/rpi001/led"
TOPIC_RPI001_STATUS = "home/rpi001/status"

# ============================================================
#  GLOBAL VARIABLES
# ============================================================
timeframe = 0
close_program = False

# LED actual states
red_state = 0
yellow_state = 0
green_state = 0

# LED blink flags
red_blink = False
yellow_blink = False
green_blink = False

# Sensor values
current_temp = 0.0
current_humi = 0.0
current_light = 0.0      # percentage
current_motion = 0

# User-defined thresholds from GUI
temp_threshold = 30.0
light_threshold = 30.0

# Light calibration 
LIGHT_RAW_MIN = 2000.0
LIGHT_RAW_MAX = 26000.0

# MPU old data
old_mpu_data = None

# Prevent repeated error spam in debug log
dht_error_logged = False
ldr_error_logged = False
mpu_error_logged = False

# Thread-safe log queue
log_queue = Queue()

# ============================================================
#  SENSOR SETUP
# ============================================================
dhtDevice = None
mpu = None
ldr_channel = None

try:
    dhtDevice = adafruit_dht.DHT22(board.D18)
    print("DHT22 setup completed")
except Exception as e:
    print("DHT22 setup error:", e)

try:
    mpu = mpu6050(0x68)
    print("MPU6050 setup completed")
except Exception as e:
    print("MPU6050 setup error:", e)

try:
    # ADS1115 + LDR on A0
    i2c = busio.I2C(board.SCL, board.SDA)
    ads = ADS.ADS1115(i2c)
    ldr_channel = AnalogIn(ads, 0)
    print("ADS1115/LDR setup completed")
except Exception as e:
    print("ADS1115/LDR setup error:", e)

# ============================================================
#  GPIO SETUP
# ============================================================
GPIO.setwarnings(False)
GPIO.cleanup()
GPIO.setmode(GPIO.BCM)

RED_LED = 17
YELLOW_LED = 27
GREEN_LED = 22

GPIO.setup(RED_LED, GPIO.OUT, initial=0)
GPIO.setup(YELLOW_LED, GPIO.OUT, initial=0)
GPIO.setup(GREEN_LED, GPIO.OUT, initial=0)

# ============================================================
#  MQTT SETUP
# ============================================================
try:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=MQTT_CLIENT_ID)
except Exception:
    client = mqtt.Client(client_id=MQTT_CLIENT_ID)

# ============================================================
#  HELPER FUNCTIONS
# ============================================================
def queue_log(msg):
    print(msg)
    log_queue.put(f"{timeframe:>3}s - {msg}")

def flush_logs():
    while True:
        try:
            msg = log_queue.get_nowait()
            txt.insert(END, "\n" + msg)
            txt.yview(END)
        except Empty:
            break

def raw_to_light_percent(raw_value):
    if LIGHT_RAW_MAX == LIGHT_RAW_MIN:
        return 0.0

    percent = ((raw_value - LIGHT_RAW_MIN) / (LIGHT_RAW_MAX - LIGHT_RAW_MIN)) * 100.0

    if percent < 0:
        percent = 0.0
    elif percent > 100:
        percent = 100.0

    return percent

def publish_sensor_data():
    try:
        client.publish(TOPIC_TEMP, f"{current_temp:.2f}")
        client.publish(TOPIC_HUMI, f"{current_humi:.2f}")
        client.publish(TOPIC_LIGHT, f"{current_light:.1f}")
        client.publish(TOPIC_MOTION, str(current_motion))
    except Exception as e:
        queue_log(f"MQTT sensor publish error: {e}")

def publish_status():
    try:
        payload = {
            "temperature": round(current_temp, 2),
            "humidity": round(current_humi, 2),
            "light": round(current_light, 1),
            "motion": current_motion,
            "temp_threshold": temp_threshold,
            "light_threshold": light_threshold,
            "green_blink": green_blink,
            "dht_ok": dhtDevice is not None,
            "mpu_ok": mpu is not None,
            "ldr_ok": ldr_channel is not None
        }
        client.publish(TOPIC_STATUS, json.dumps(payload))
    except Exception as e:
        queue_log(f"MQTT status publish error: {e}")

# ============================================================
#  MQTT CALLBACKS
# ============================================================
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        queue_log("MQTT connected to broker")
        client.subscribe(TOPIC_LED_COMMAND)
        client.subscribe(TOPIC_RPI001_STATUS)
    else:
        queue_log(f"MQTT connection failed (rc={rc})")
        
def on_disconnect(client, userdata, rc):
    if rc == 0:
        queue_log("MQTT disconnected normally")
    else:
        queue_log(f"MQTT disconnected unexpectedly (rc={rc})")

def on_message(client, userdata, msg):
    global green_blink

    topic = msg.topic
    payload = msg.payload.decode().strip().lower()

    try:
        if topic == TOPIC_RPI001_STATUS:
            if payload == "online":
                queue_log("Rpi001 online")
            elif payload == "offline":
                queue_log("Rpi001 offline")

        elif topic == TOPIC_LED_COMMAND:
            if payload in ["1", "on", "blink", "start"]:
                green_blink = True
                queue_log("Green LED blinking from Rpi001")
            elif payload in ["0", "off", "stop"]:
                green_blink = False
                queue_log("Green LED stopped by Rpi001")

    except Exception as e:
        queue_log(f"MQTT message error: {e}")

client.on_connect = on_connect
client.on_disconnect = on_disconnect
client.on_message = on_message

# ============================================================
#  WINDOW CONTROL
# ============================================================
def clo_se():
    global close_program
    res = messagebox.askquestion("Close?", "Really really wanna close?")
    if res == "yes":
        close_program = True

# ============================================================
#  TIME UPDATE
# ============================================================
def timeup():
    global timeframe
    timeframe += 1

# ============================================================
#  LED FUNCTIONS
# ============================================================
def setRedLed(state):
    global red_state
    red_state = 1 if int(state) else 0
    GPIO.output(RED_LED, red_state)

    redbutton.config(
        text="ON" if red_state else "OFF",
        bg="#ff2244" if red_state else "#1a0a0a",
        fg="#ffffff" if red_state else "#ff2244",
        relief="sunken" if red_state else "flat"
    )

def setYellowLed(state):
    global yellow_state
    yellow_state = 1 if int(state) else 0
    GPIO.output(YELLOW_LED, yellow_state)

    yellowbutton.config(
        text="ON" if yellow_state else "OFF",
        bg="#ccaa00" if yellow_state else "#1a1500",
        fg="#ffffff" if yellow_state else "#ccaa00",
        relief="sunken" if yellow_state else "flat"
    )

def setGreenLed(state):
    global green_state
    green_state = 1 if int(state) else 0
    GPIO.output(GREEN_LED, green_state)

    greenbutton.config(
        text="ON" if green_state else "OFF",
        bg="#00cc66" if green_state else "#001a0d",
        fg="#ffffff" if green_state else "#00cc66",
        relief="sunken" if green_state else "flat"
    )

def blink_update():
    if red_blink:
        setRedLed(0 if red_state else 1)
    else:
        setRedLed(0)

    if yellow_blink:
        setYellowLed(1)
    else:
        setYellowLed(0)

    if green_blink:
        setGreenLed(0 if green_state else 1)
    else:
        setGreenLed(0)

# ============================================================
#  THRESHOLD APPLY FROM GUI
# ============================================================
def apply_thresholds():
    global temp_threshold, light_threshold

    try:
        temp_threshold = float(temp_threshold_var.get())
        th_temp_value.config(text=f"{temp_threshold:.1f}")
        queue_log(f"GUI temp threshold = {temp_threshold:.1f}")
    except ValueError:
        queue_log("Invalid GUI temp threshold")

    try:
        light_threshold = float(light_threshold_var.get())
        th_light_value.config(text=f"{light_threshold:.1f}")
        queue_log(f"GUI light threshold = {light_threshold:.1f}%")
    except ValueError:
        queue_log("Invalid GUI light threshold")

# ============================================================
#  DHT22 UPDATE
# ============================================================
def temphumiupdate():
    global current_temp, current_humi, dht_error_logged

    if dhtDevice is None:
        try:
            temtext.config(text="N/A", fg="#ff4444")
            humtext.config(text="N/A", fg="#ff4444")
        except:
            pass

        if not dht_error_logged:
            queue_log("DHT22 not available. Check wiring/library.")
            dht_error_logged = True
        return

    try:
        temp = dhtDevice.temperature
        humi = dhtDevice.humidity

        if temp is not None and humi is not None:
            current_temp = float(temp)
            current_humi = float(humi)

            temtext.config(text=f"{current_temp:.1f}")
            humtext.config(text=f"{current_humi:.1f}")

            # Temperature colour
            if current_temp < 16:
                temtext.config(fg="#00eeff")
            elif current_temp < 35:
                temtext.config(fg="#00ff99")
            else:
                temtext.config(fg="#ff4444")

            # Humidity colour
            if current_humi < 25:
                humtext.config(fg="#ffaa00")
            elif current_humi < 60:
                humtext.config(fg="#00ff99")
            else:
                humtext.config(fg="#00cfff")

    except Exception as e:
        queue_log(f"DHT22 error: {e}")

# ============================================================
#  LIGHT UPDATE
# ============================================================
def light_update():
    global current_light, ldr_error_logged

    if ldr_channel is None:
        try:
            lighttext.config(text="N/A", fg="#ff4444")
        except:
            pass

        if not ldr_error_logged:
            queue_log("LDR/ADS1115 not available. Check wiring/I2C.")
            ldr_error_logged = True
        return

    try:
        raw_value = float(ldr_channel.value)
        current_light = raw_to_light_percent(raw_value)

        lighttext.config(text=f"{current_light:.1f}")

        if current_light < 30:
            lighttext.config(fg="#00cfff")
        elif current_light < 70:
            lighttext.config(fg="#00ff99")
        else:
            lighttext.config(fg="#ffaa00")

    except Exception as e:
        queue_log(f"LDR error: {e}")

# ============================================================
#  MPU6050 UPDATE
# ============================================================
def MPU_update():
    global mpu_error_logged

    if mpu is None:
        try:
            acctxtX.config(text="N/A")
            acctxtY.config(text="N/A")
            acctxtZ.config(text="N/A")
            gyrtxtX.config(text="N/A")
            gyrtxtY.config(text="N/A")
            gyrtxtZ.config(text="N/A")
        except:
            pass

        if not mpu_error_logged:
            queue_log("MPU6050 not available. Check wiring/I2C.")
            mpu_error_logged = True
        return None

    accel = mpu.get_accel_data()
    gyro = mpu.get_gyro_data()

    accX = accel["x"]
    accY = accel["y"]
    accZ = accel["z"]
    gyrX = gyro["x"]
    gyrY = gyro["y"]
    gyrZ = gyro["z"]

    acctxtX.config(text=f"{accX:.3f}")
    acctxtY.config(text=f"{accY:.3f}")
    acctxtZ.config(text=f"{accZ:.3f}")
    gyrtxtX.config(text=f"{gyrX:.3f}")
    gyrtxtY.config(text=f"{gyrY:.3f}")
    gyrtxtZ.config(text=f"{gyrZ:.3f}")

    return (accX, accY, accZ, gyrX, gyrY, gyrZ)

def MPU_detect(old, new, threshold=0.5):
    if old is None or new is None:
        return False

    for i in range(6):
        if abs(new[i] - old[i]) > threshold:
            return True
    return False

def MPU_motion():
    global old_mpu_data, current_motion

    try:
        new_data = MPU_update()

        if new_data is None:
            return

        if old_mpu_data is None:
            old_mpu_data = new_data
            return

        detected = MPU_detect(old_mpu_data, new_data, threshold=1.0)

        if detected and current_motion == 0:
            current_motion = 1
            motionText.config(text="DETECTED", fg="#ff4444")
            client.publish(TOPIC_MOTION, "1")
            queue_log("Motion detected")

        elif not detected and current_motion == 1:
            current_motion = 0
            motionText.config(text="CLEAR", fg="#00ff99")
            client.publish(TOPIC_MOTION, "0")
            queue_log("Motion cleared")

        old_mpu_data = new_data

    except Exception as e:
        queue_log(f"MPU error: {e}")

# ============================================================
#  AUTOMATION
# ============================================================
def automation_check():
    global red_blink, yellow_blink

    if current_temp > temp_threshold:
        red_blink = True
    else:
        red_blink = False
        setRedLed(0)

    if current_light < light_threshold:
        yellow_blink = True
    else:
        yellow_blink = False
        setYellowLed(0)

# ============================================================
#  THEME
# ============================================================
BG = "#0d0f14"
PANEL_BG = "#13171f"
BORDER = "#1e2530"
ACCENT = "#00ffcc"
ACCENT2 = "#00cfff"
TEXT_DIM = "#4a5568"

FONT_MONO = ("Courier", 12)
FONT_LABEL = ("Courier", 11, "bold")
FONT_TITLE = ("Courier", 13, "bold")
FONT_VALUE = ("Courier", 14, "bold")

def make_panel(parent, title, row, col, rowspan=1, colspan=1):
    f = LabelFrame(
        parent,
        text=f"  {title}  ",
        bg=PANEL_BG,
        fg=ACCENT,
        font=FONT_TITLE,
        bd=1,
        relief="solid",
        highlightbackground=BORDER,
        highlightthickness=1,
        labelanchor="nw",
    )
    f.grid(row=row, column=col, rowspan=rowspan, columnspan=colspan,
           sticky="nsew", padx=6, pady=6)
    return f

def styled_label(parent, text, row, col, sticky="e", fg=TEXT_DIM, font=FONT_LABEL):
    lbl = Label(parent, text=text, bg=PANEL_BG, fg=fg, font=font)
    lbl.grid(row=row, column=col, sticky=sticky, padx=5, pady=3)
    return lbl

def value_label(parent, row, col, width=10):
    lbl = Label(
        parent, text="--", width=width, bg="#080b10",
        fg=ACCENT, font=FONT_VALUE, relief="flat",
        bd=0, padx=6, pady=3, anchor="center"
    )
    lbl.grid(row=row, column=col, sticky="w", padx=5, pady=3)
    return lbl

# ============================================================
#  WINDOW
# ============================================================
win = Tk()
win.title("◈ SMART HOME Rpi002")
win.geometry("900x650+150+100")
win.configure(bg=BG)
win.resizable(False, False)
win.protocol("WM_DELETE_WINDOW", clo_se)

fra = Frame(win, bg=BG)
fra.pack(fill="both", expand=True, padx=10, pady=10)

for c in range(2):
    fra.columnconfigure(c, weight=1)

temp_threshold_var = StringVar(value=str(temp_threshold))
light_threshold_var = StringVar(value=str(light_threshold))

# ============================================================
#  LED STATUS PANEL
# ============================================================
ledSector = make_panel(fra, "LED STATUS", 0, 0)
ledSector.columnconfigure((0, 1, 2), weight=1)

styled_label(ledSector, "TEMP LED", 0, 0, sticky="n", fg="#ff2244")
redbutton = Button(
    ledSector, text="OFF", width=8,
    bg="#1a0a0a", fg="#ff2244",
    font=FONT_LABEL, relief="flat", bd=0,
    state="disabled"
)
redbutton.grid(row=1, column=0, padx=8, pady=6)

styled_label(ledSector, "LIGHT LED", 0, 1, sticky="n", fg="#ccaa00")
yellowbutton = Button(
    ledSector, text="OFF", width=8,
    bg="#1a1500", fg="#ccaa00",
    font=FONT_LABEL, relief="flat", bd=0,
    state="disabled"
)
yellowbutton.grid(row=1, column=1, padx=8, pady=6)

styled_label(ledSector, "MQTT LED", 0, 2, sticky="n", fg="#00cc66")
greenbutton = Button(
    ledSector, text="OFF", width=8,
    bg="#001a0d", fg="#00cc66",
    font=FONT_LABEL, relief="flat", bd=0,
    state="disabled"
)
greenbutton.grid(row=1, column=2, padx=8, pady=6)

# ============================================================
#  ENVIRONMENT PANEL
# ============================================================
envSector = make_panel(fra, "ENVIRONMENT", 0, 1)
envSector.columnconfigure((0, 1, 2), weight=1)

styled_label(envSector, "TEMP", 0, 0)
temtext = value_label(envSector, 0, 1, 8)
styled_label(envSector, "°C", 0, 2, sticky="w", fg=ACCENT2)

styled_label(envSector, "HUMI", 1, 0)
humtext = value_label(envSector, 1, 1, 8)
styled_label(envSector, "%", 1, 2, sticky="w", fg=ACCENT2)

styled_label(envSector, "LIGHT", 2, 0)
lighttext = value_label(envSector, 2, 1, 8)
styled_label(envSector, "%", 2, 2, sticky="w", fg=ACCENT2)

# ============================================================
#  THRESHOLD PANEL
# ============================================================
thSector = make_panel(fra, "THRESHOLDS", 1, 1)
thSector.columnconfigure((0, 1, 2), weight=1)

styled_label(thSector, "TEMP TH", 0, 0)
temp_entry = Entry(
    thSector, textvariable=temp_threshold_var,
    width=10, font=FONT_LABEL,
    bg="#080b10", fg=ACCENT, insertbackground=ACCENT,
    relief="flat", justify="center"
)
temp_entry.grid(row=0, column=1, padx=5, pady=5)
styled_label(thSector, "°C", 0, 2, sticky="w", fg=ACCENT2)

styled_label(thSector, "LIGHT TH", 1, 0)
light_entry = Entry(
    thSector, textvariable=light_threshold_var,
    width=10, font=FONT_LABEL,
    bg="#080b10", fg=ACCENT, insertbackground=ACCENT,
    relief="flat", justify="center"
)
light_entry.grid(row=1, column=1, padx=5, pady=5)
styled_label(thSector, "%", 1, 2, sticky="w", fg=ACCENT2)

apply_button = Button(
    thSector, text="APPLY",
    bg="#003344", fg="#ffffff",
    font=FONT_LABEL, relief="flat", bd=0,
    padx=10, pady=4,
    command=apply_thresholds
)
apply_button.grid(row=2, column=0, columnspan=3, pady=8)

styled_label(thSector, "TEMP NOW", 3, 0)
th_temp_value = value_label(thSector, 3, 1, 8)
th_temp_value.config(text=f"{temp_threshold:.1f}")

styled_label(thSector, "LIGHT NOW", 4, 0)
th_light_value = value_label(thSector, 4, 1, 8)
th_light_value.config(text=f"{light_threshold:.1f}")

styled_label(thSector, "MOTION", 5, 0)
motionText = value_label(thSector, 5, 1, 10)
motionText.config(text="CLEAR", fg="#00ff99")

# ============================================================
#  MPU PANEL
# ============================================================
mpuSector = make_panel(fra, "MPU6050", 2, 0, colspan=2)
mpuSector.columnconfigure((0, 1, 2, 3, 4, 5), weight=1)

for col_i, label in enumerate(["ACC-X", "ACC-Y", "ACC-Z", "GYR-X", "GYR-Y", "GYR-Z"]):
    styled_label(mpuSector, label, 0, col_i, sticky="n", fg=ACCENT2)

acctxtX = value_label(mpuSector, 1, 0, 9)
acctxtY = value_label(mpuSector, 1, 1, 9)
acctxtZ = value_label(mpuSector, 1, 2, 9)
gyrtxtX = value_label(mpuSector, 1, 3, 9)
gyrtxtY = value_label(mpuSector, 1, 4, 9)
gyrtxtZ = value_label(mpuSector, 1, 5, 9)

# ============================================================
#  DEBUG LOG PANEL
# ============================================================
txtbox = make_panel(fra, "DEBUG LOG", 1, 0)

txt = scrolledtext.ScrolledText(
    txtbox, width=42, height=12,
    bg="#080b10", fg="#00ffcc",
    insertbackground=ACCENT,
    font=FONT_MONO,
    relief="flat", bd=0,
    selectbackground="#1e2530",
)
txt.grid(row=0, column=0, padx=8, pady=8)

# ============================================================
#  INITIAL CALLS
# ============================================================
temphumiupdate()
light_update()
old_mpu_data = MPU_update()
queue_log("System started")

# ============================================================
#  SCHEDULE
# ============================================================
schedule.every(1).seconds.do(timeup)
schedule.every(1).seconds.do(MPU_motion)
schedule.every(3).seconds.do(temphumiupdate)
schedule.every(3).seconds.do(light_update)
schedule.every(1).seconds.do(automation_check)
schedule.every(0.5).seconds.do(blink_update)
schedule.every(3).seconds.do(publish_sensor_data)
schedule.every(5).seconds.do(publish_status)

# ============================================================
#  START MQTT
# ============================================================
try:
    client.reconnect_delay_set(min_delay=1, max_delay=10)
    client.connect_async(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_start()
except Exception as e:
    queue_log(f"MQTT startup error: {e}")

# ============================================================
#  MAIN LOOP
# ============================================================
LOOP_ACTIVE = True

while LOOP_ACTIVE:
    try:
        schedule.run_pending()
        flush_logs()

        win.update_idletasks()
        win.update()

        if close_program:
            setRedLed(0)
            setYellowLed(0)
            setGreenLed(0)

            try:
                client.loop_stop()
                client.disconnect()
            except:
                pass

            win.quit()
            GPIO.cleanup()
            LOOP_ACTIVE = False

        time.sleep(0.05)

    except KeyboardInterrupt:
        GPIO.cleanup()
        try:
            client.loop_stop()
            client.disconnect()
        except:
            pass
        break

    except RuntimeError as e:
        queue_log(f"Runtime error: {e}")
        time.sleep(0.1)

    except Exception as e:
        queue_log(f"General error: {e}")
        time.sleep(0.1)
