#!/usr/bin/python

import time
import datetime
import serial
import re
#import subprocess
import string
from ConfigParser import SafeConfigParser
from subprocess import Popen, PIPE
import paho.mqtt.publish as publish


### CONSTANTS

acceptableMessageTimeWindow = .016 # seconds... remember that Linux is not realtime
repeatTime = .750 # seconds... remember Linux is not realtime
  # the repeatTime is vary long becase the PHR03 sends a single keypress for a long
  # time and it not desired to call a single key press multiple espeically for
  # DIM and BRIGHTEN

NoSubtopic = '' # flag to not sent MQTT message

houseCode = [ # this list is treated as an indexed array
  'M', #0x00
  'E', #0x01
  'C', #0x02
  'K', #0x03
  'O', #0x04
  'G', #0x05
  'A', #0x06
  'I', #0x07
  'N', #0x08
  'F', #0x09
  'D', #0x0A
  'L', #0x0B
  'P', #0x0C
  'H', #0x0D
  'B', #0x0E
  'J'  #0x0F
] 

level = {
  "DEBUG":   1, # -- all debug messages
  "DEBUG 3": 2, # -- dump of raw message bytes
  "DEBUG 2": 3, # -- dump of flipped message bytes
  "DEBUG 1": 4, # -- decode of all messages received

  "INFO":    5, # -- decode of minimized messages
  "WARNING": 6, # -- detection of transient, fixable errors and possible interference
  "ERROR":   7  # -- detection of hard  error condition
}


### GLOBALS
subtopic = NoSubtopic
commandCodePressed = ''
command = ''

### set up default values
portID = '' # /dev/ttyUSB0
logFile = '' # standard output
loggingLevel = level['ERROR']
MQTT_brokerHost = "localhost"
MQTT_QOS = 0
MQTT_retention = True
MQTT_mapping = []



### read the configuration file

config = SafeConfigParser()
config.readfp(open('W800.conf'))

try:
  sections = config.sections()
  print "sections:", sections
  for section in sections:
    options = config.options(section)
    print "section, options:", section, ":", options
    for option in options:
      option_value = config.get (section, option)
      print "section:options:  ", section + ": " + option + ": " + option_value
      if section == 'ports':
        print "  ports: option", option, config.get (section, option)
        if option == 'portid':
          portID = config.get(section, option)
        else:
          print "Unexpected option in ports: " + option

      elif section == 'Logging':
        print "  logging: option", option, config.get (section, option)
        if option == 'logginglevel':
          loggingLevelName = config.get(section, option)
          print "logginglevelName", loggingLevelName
          loggingLevel = level [loggingLevelName]
        elif option == 'logFile':
          logFile = config.get(section, option)
        else:
          print "Unexpected option in logging: " + option

      elif section == 'MQTT':
        print "  mqtt: option", option, config.get (section, option)
        if option == 'mqtt_brokerhost':
          MQTT_brokerHost = config.get(section, option)
        elif option == 'mqtt_qos':
          MQTT_QOS = config.getint(section, option)
        elif option == 'mqtt_topic':
          MQTT_topic = config.get(section, option)
        elif option == 'mqtt_mapping':
          MQTT_mapping = config.get(section, option)
        else:
          print "Unexpected option in mqtt: " + option
      else:
        print "Unexpected section: " + section
except:
  print "The value for option '" + option + "' in section '" + section + "' isn't right"


def command(port):
    return ["ls", "-l", "/dev/serial/by-id/" + port]


def findPort(port):
  proc = Popen(command(port), stdout=PIPE, stderr=PIPE, universal_newlines=True)
  result = ""
  exit_code = proc.wait()
  if exit_code != 0:
    for line in proc.stderr:
      result = result + line
  else:
    for line in proc.stdout:
      result = result + line
      index = result.find( "ttyUSB")
      result = "/dev/" + result [index : index + 7]
  return result
    
if portID == "":
  USBport = "/dev/ttyUSB0" # assume there is only one USB port and you are using it
else:
  USBport = findPort( portID)

# convert the MQTT_mapping string to a list
MQTT_mappingList = []
parser = re.compile("\s*([A-P][0-9]{1,2}|DIM|BRIGHT)\s*([^#]*)\s*(#.*)?")
    # look for X10 housecode and unit number
    # look for optional alias (without # comment)
    # look for optional comment
units = MQTT_mapping.split( "\n") # assumes one per line, last line with only ']'
for unit in units:
  if ']' not in unit:
    result = parser.match (unit)
    if result is not None:
       code = result.group (1) # house code and unit number
       if result.group(2) is not '': # alias
         # change tabs and spaces to underscore, except at begin or end
         topicString = result.group(2).replace(' ','_')
         topicString = topicString.replace('\t','_').strip('_')
       else:
         topicString = code # default alias, to let downstream app do mapping
       MQTT_mappingList.append ((code, topicString))


### confirm the setting of the defaults ....CONFIG DEBUG ONLY
#print "portID:", portID
#print "USBport:", USBport
#print "loggingLevel:", loggingLevel
#print "logFile:", logFile
#print "MQTT_brokerHost:", MQTT_brokerHost
#print "MQTT_QOS:", MQTT_QOS
#print "MQTTtopic:", MQTT_topic
#print "MQTT_mappingList:", MQTT_mappingList



### support functions


def mapping( unit):
  if MQTT_mappingList == []:
    return unit
  else:
    for row in MQTT_mappingList:
      if row[0] == unit:
        return row[1]
    return ''


def log(level, message):
  # level is a string like 'DEBUG1', 'WARN', 'ERROR'
  # message is the message to be logged
  
  #print time level message
  if level >= loggingLevel:
    ndt= datetime.datetime.fromtimestamp( now)
    timeString = datetime.datetime.strftime(ndt, "%X") 
    print "{0:s}.{1:03d} {2:7s} {3:s}".format(timeString, ndt.microsecond/1000, level, message)
    return


def swapBitOrder(inChar):
  inMask =  0b10000000
  outMask = 0b00000001
  outChar = 0

  # swap the bit order
  for i in range (0,8):
    if inChar & inMask:
      outChar = outChar | outMask
    outMask = outMask << 1
    inMask = inMask >> 1
  return outChar


def recentRepeat(message):
  global eventTime
  global eventMessage
  global messageTime

  if eventTime == 0: # first pass, so not a repeat
    eventTime = messageTime [0] # time of the first byte of the message
    eventMessage = message
  else:
    if message == eventMessage:
      if messageTime[0] - eventTime < repeatTime: # part of the current burst
        if loggingLevel <= level['DEBUG 2']:
          return True
        else:
          return False # trick to force logging of all messages
      else:
        eventTime = messageTime [0] # set up new time of the event
        return False
    else:
      eventMessage =  message # set up new message
      eventTime = messageTime [0] # set up new time of the event
      return False


def discardOldestMessageByte():
  global messageTime
  global intermediate
  global raw
  global count

  messageTime[0] = messageTime[1]
  messageTime[1] = messageTime[2]
  messageTime[2] = messageTime[3]
  raw[0] = raw[1]
  raw[1] = raw[2]
  raw[2] = raw[3]


  
def logReceiptW800message():
  global command

  logMessage = ""
  # prepare logging message with raw inputs, as appropriate
  if loggingLevel <= level['DEBUG 3']:
    for count in range (0,4):
      logMessage += "{0:02X} ".format( raw[count])
    logMessage += "> "
  if loggingLevel <= level['DEBUG 2']:
    for count in range (0,4):
      logMessage += "{0:02X} ".format( intermediate[count])
    logMessage += "> "
  if loggingLevel <= level['DEBUG 1']:
    for count in range (0,4):
      logMessage += "{0:02X} ".format( rearranged[count])
  logMessage += " " + command
  if logMessage is not "":
    log( "DEBUG 1", "Received W800 " + logMessage)


def decodeX10message():
  '''
bit  function
                                        00000000
7    always 0
6    always 0
5    always 0                               |<--- from bit 5 of byte 3
4    bit 1 of unit number ------------------->|
3    bit 0 of unit number -------------------->|       
2    1 for OFF command.                     xxxx <--(unit number -1)
1    bit 2 of unit number ------------------>|
0    1 for DIM (if bit 3=1) or BRIGHT (if bit 3=0) command
  '''
  global subtopic
  global commandCodePressed
  global command

  name = ''
  houseCodePressed = houseCode[ rearranged[2] & 0x0F]  # only 4 bits are significant
  if rearranged[0] & 0b00000001 == 0:
    unitCodePressed = (rearranged[2] & 0b00100000) >> 2 # 0b00001000
    if rearranged[0] & 0b00001000:
      unitCodePressed = unitCodePressed | 0b00000001
    if rearranged[0] & 0b00010000:
      unitCodePressed = unitCodePressed | 0b00000010
    if rearranged[0] & 0b00000010:
      unitCodePressed = unitCodePressed | 0b00000100
    unitCodePressed = unitCodePressed + 1
    if rearranged[0] & 0b00000100 == 0b00000100:
      commandCodePressed = 'OFF'
    else:
      commandCodePressed = 'ON'
    command = houseCodePressed + str(unitCodePressed) + " " + commandCodePressed
    unit =  houseCodePressed + str(unitCodePressed)
    
    alias = mapping( unit)
    if alias is not '':
      subtopic = alias
    else:
      subtopic = NoSubtopic # flag to not send MQTT
    command = unit + " " + commandCodePressed
  else:
    if rearranged[0] == 0x11:
      commandCodePressed = 'BRIGHT'
    elif rearranged[0] == 0x19:
      commandCodePressed = 'DIM'

    command = commandCodePressed
    alias = mapping( command)
    if alias is not '':
      subtopic = alias
    else:
      subtopic = NoSubtopic # flag to not send MQTT


def decodeX10security ():
  global command
  global commandCodePressed
  global subtopic

  if rearranged[0] & 0xFE == 0x20: # unit is a DS10A
    if rearranged[0] & 0x01 == 0:
      commandCodePressed = "OPEN"
    else:
      commandCodePressed = "CLOSED"
    unit = "DS" + str(rearranged[3])
    command = unit + " " + commandCodePressed
    alias = mapping( unit)
    if alias is not '':
      subtopic = alias
    else:
      subtopic = NoSubtopic # flag to not send MQTT
  else:
    # unknown X10 security device
    commandCodePressed = "UNKNOWN"
    unitNumber = "Unknown"
    subtopic = NoSubtopic # flag to not send MQTT




### main loop

# Establish the connection on a specific port
ser = serial.Serial(USBport, 4800) 

now = time.time()
log ("INFO", "W800 Starting up...")
eventTime = 0 # to mark the first pass

while True:
  messageTime = [.0,.0,.0,.0]
  raw =          [0,0,0,0]
  intermediate = [0,0,0,0]
  rearranged =   [0,0,0,0]
  count = 0
  while count < 4:
    raw[ count] = ord(ser.read()) # Read the newest output 
    now = time.time()
    messageTime[ count] = now

    

    count = count + 1
    if count == 4: # whole message has been received
      intermediate[ 0] = swapBitOrder( raw[ 0])
      intermediate[ 1] = swapBitOrder( raw[ 1])
      intermediate[ 2] = swapBitOrder( raw[ 2])
      intermediate[ 3] = swapBitOrder( raw[ 3])

      # change the byte order 0123 to 2301
      rearranged[ 0] = intermediate[ 2]
      rearranged[ 1] = intermediate[ 3]
      rearranged[ 2] = intermediate[ 0]
      rearranged[ 3] = intermediate[ 1]

      #check if message transferred together
      if messageTime[3] - messageTime[0] > acceptableMessageTimeWindow:
        log("WARNING", "Time disparity, discarding {0:02X} > {1:02X}".format(raw[0], intermediate[0]))
        discardOldestMessageByte
        count = 3

      #check if this is an X10 message
      elif rearranged[0] ^ rearranged[1] == 0xFF and rearranged[2] ^ rearranged[3] == 0xFF: # potentially X10 format
        if rearranged[0] & 0xE0 == 0:  # X10 format
          if not recentRepeat(rearranged[ 0] << 8 | rearranged[ 2] ):
            decodeX10message ()
          else:
            count = 0 # discard message silently, unless debugging
        else:
          log( "WARN", "X10 message not recognized, discarding {0:02X} > {1:02X}".format(raw[0], intermediate[0]))
          discardOldestMessageByte()
          count = 3

      # check if this is an X10 PRO message
      elif rearranged[0] ^ rearranged[1] == 0xff and rearranged[2] ^ rearranged[3] == 0xf0: # is the X10 security format
        if not recentRepeat(rearranged[ 0] << 8 | rearranged[ 3] ):
          decodeX10security ()
        else:
          count = 0 # discard message silently, unless debugging

      # handle unrecognized messages
      else:
        log( "WARN", "message not recognized, discarding {0:02X} > {1:02X}".format(raw[0], intermediate[0]))
        discardOldestMessageByte()
        count = 3
      logReceiptW800message()
  if subtopic is not NoSubtopic:
    log( "INFO", "Sending MQTT message: /" + MQTT_topic + subtopic + ":" + commandCodePressed)
    publish.single(MQTT_topic + subtopic, commandCodePressed, hostname=MQTT_brokerHost)
