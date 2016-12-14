**Introduction**

W800.py is a Python program to scan a WGL Designs W800 32-bit X10 radio receiver attached to a USB port. It converts the messages sent by X10 wireless and X10 security components to an internal format (house code A, unit code 1, on or the DS10A unit number and state (open or closed)) into a MQTT message that is sent to a broker.

This has been tested to work with the following devices:

* PHR02 8-unit handheld controller
* MS13A PIR motion detector and darkness detector
* DS10A Security door/window monitor

Program runs on a Raspberry Pi to an MQTT broker (mosquitto) running on another machine in the same local network.

Decoding is based on the data format documentation on the WGL website http://www.wgldesigns.com/protocols/w800rf32_protocol.txt.

**Configuration**

W800 has several options that are edited within the W800.conf file. This allows the user to change parameters without changing the W800 file.

User can whitelist the units to pass along to the MQTT broker or pass all units.

Logging options allow for more information to be sent to the log file for improved decoding of messages.

**Usage**

File locations:

* Program file W800.py in /usr/bin
* Configuration file W800.conf in /etc
* some sort of startup file W800 maybe in /etc/conf.d
* Log file in /var/log/w800log

**Warning:**

This is a work in progress and is not fully developed or tested.