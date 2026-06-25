# smart-home-iot-raspberry-pi

# SMART Home System using Raspberry Pi

## Overview

This project is an IoT-based SMART Home System developed using two Raspberry Pi boards. The system provides security, monitoring, and automation functions through sensor data collection, MQTT communication, Telegram bot control, and Blynk cloud monitoring.

The system is divided into two main units. Rpi001 acts as the gateway that communicates with Telegram, Blynk, and Rpi002. Rpi002 acts as the sensor node that collects sensor readings and controls output devices.

## Features

### Security

* Motion detection using MPU6050
* Automatic motion alert sent to Telegram and Blynk

### Monitoring

* Temperature and humidity monitoring using DHT22
* Light intensity monitoring using LDR and ADS1115
* Sensor readings displayed on Rpi002 GUI
* Remote sensor monitoring through Telegram and Blynk

### Automation

* LED control based on temperature threshold
* LED control based on light intensity threshold
* Remote LED control through Telegram command

## System Architecture

The system uses two Raspberry Pi boards:

```text
Rpi002 Sensor Node  <--MQTT-->  Rpi001 Gateway  <--Internet-->  Telegram / Blynk
```

### Rpi001 Gateway

* Receives sensor data from Rpi002 using MQTT
* Uploads sensor readings to Blynk
* Sends motion alerts to Telegram
* Handles Telegram commands for checking sensor readings and controlling LEDs

### Rpi002 Sensor Node

* Reads temperature and humidity from DHT22
* Reads motion data from MPU6050
* Reads light intensity using LDR and ADS1115
* Displays sensor readings using GUI
* Controls LEDs based on thresholds and Telegram commands

## Technologies Used

* Raspberry Pi
* Python
* MQTT
* Telegram Bot
* Blynk IoT
* Tkinter GUI
* DHT22 Sensor
* MPU6050 Sensor
* LDR + ADS1115
* GPIO LED Control

## My Contributions

* Developed Python programs for sensor reading, MQTT communication, GUI display, and LED control.
* Implemented communication between two Raspberry Pi boards using MQTT protocol.
* Integrated Telegram bot commands for remote sensor checking and LED control.
* Connected sensor data to Blynk for remote IoT monitoring.
* Implemented automation logic based on temperature and light intensity thresholds.
* Tested and debugged system communication, sensor readings, Telegram alerts, and LED responses.

## Project Structure

```text
rpi001.py   Source code for gateway Raspberry Pi
rpi002.py   Source code for sensor-node Raspberry Pi
docs/       System architecture and setup documentation slides
```

## Hardware Components

* Raspberry Pi x2
* DHT22 temperature and humidity sensor
* MPU6050 accelerometer/gyroscope sensor
* LDR light sensor
* ADS1115 ADC module
* LEDs
* Resistors
* Jumper wires
* Breadboard

## Status

Completed
