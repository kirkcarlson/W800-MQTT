#!/usr/bin/python

#
### IMPORTS
#
import time
import datetime
import serial
import re
import string
from ConfigParser import SafeConfigParser
from subprocess import Popen, PIPE
import paho.mqtt.publish as publish

#
### CONSTANTS
#
acceptableMessageTimeWindow = .02 # seconds... remember that Linux is not realtime
REPEAT_TIME = .55 # seconds... remember Linux is not realtime
  # the REPEAT_TIME is vary long becase the PHR03 sends multiple messages for a single
  # keypress for a long time and it not desired to call a single key press multiple
  # especially for DIM and BRIGHTEN, better to slow it down a bit

NO_SUBTOPIC = '' # special topic used to not send individual MQTT message

houseCode = [ # The house codes are used by X10 for a higher level separation over unit codes
              # This list is treated as an indexed array.
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

loggingLevels = { # dictionary of possible logging levels
  "DEBUG":   1, # -- all debug messages
  "DEBUG 4": 2, # -- dump of each received byte
  "DEBUG 3": 3, # -- dump of raw message bytes
  "DEBUG 2": 4, # -- dump of flipped message bytes
  "DEBUG 1": 5, # -- decode of all messages received

  "INFO":    6, # -- decode of minimized messages
  "WARNING": 7, # -- detection of transient, fixable errors and possible interference
  "ERROR":   8  # -- detection of hard  error condition
}


#
### GLOBALS
#
subtopic = NO_SUBTOPIC
commandCodePressed = ''
command = ''
now = 0            # the time of the current message byte being processed
intermediate = []  # an intermediate buffer of the last four bytes received 
rearranged = []    # a rearragement buffer of the last four bytes received, this is the X10 message

# default values for configured globals
portID =           '' # /dev/ttyUSB0
logFile =          '' # standard output
loggingLevel =     loggingLevels['ERROR']
logFile =          ''
MQTT_brokerHost =  "localhost"
MQTT_QOS =         0
MQTT_retention =   True
MQTT_mapString =   "" # if configured enables selective sending and optionally renaming
MQTT_mapping =     []
MQTT_topic =       ""



#
### FUNCTIONS
#


def configure_parameters():
  global portID
  global logginglevelName
  global loggingLevel
  global logFile
  global MQTT_brokerHost
  global MQTT_QOS
  global MQTT_retention
  global MQTT_mapString
  global MQTT_mapping
  global MQTT_topic

  config = SafeConfigParser()
  config.readfp(open('w800.conf'))
  
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
            loggingLevel = loggingLevels [loggingLevelName]
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
            MQTT_mapString = config.get(section, option)
          else:
            print "Unexpected option in mqtt: " + option
        else:
          print "Unexpected section: " + section
  except:
    print "The value for option '" + option + "' in section '" + section + "' isn't right"


def command(port):
    return ["ls", "-l", "/dev/serial/by-id/" + port]


def findPort(port): # find the USB port connected to a serial device
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


def getPort( portPath):
  if portPath == "":
    port = "/dev/ttyUSB0" # since no name supplied, assume there is only one USB port and you are using it
  else:
    port = findPort( portPath)
  return port




def convertMQTTmappingStringToList (mappingString):
  global MQTT_mappingList

  # convert the MQTT_mapping string to a list
  # this allows the sending of names like "Living Room Motion" rather than the code "A10"
  MQTT_mappingList = []
  parser = re.compile("\s*([A-P][0-9]{1,2}|DIM|BRIGHT)\s*([^#]*)\s*(#.*)?")
      # this is a string with fields on a line separated by white space
      #   X10 housecode letter and unit number (1-16) OR 'DIM' OR 'BRIGHT'
      #   optional alias (without # comment)
      #   # optional comment
  x10units = mappingString.split( "\n") # assumes one per line, last line with only ']'
  for x10unit in x10units:
    if ']' not in x10unit:
      result = parser.match (x10unit)
      if result is not None:
         code = result.group (1) # x10 house code and unit number OR 'DIM' OR 'BRIGHT'
         if result.group(2) is not '': # alias
           # change tabs and spaces to underscore, except at begin or end
           topicString = result.group(2).replace(' ','_')
           topicString = topicString.replace('\t','_').strip('_')
         else:
           topicString = code # default alias, to let downstream app do mapping
         MQTT_mappingList.append ((code, topicString))


# optionally convert X10 house code and unit code to name or none
def unitMapping( unit):
  if MQTT_mappingList != []:
    for row in MQTT_mappingList:
      if row[0] == unit:
        return row[1]
    return ''
  return unit


def log(level, message):
  # level is a string like 'DEBUG1', 'WARNING', 'ERROR'
  # message is the message to be logged

  global now;
  global loggingLevels;
  
  #print: time level message
  if loggingLevels[level] >= loggingLevel:
    ndt= datetime.datetime.fromtimestamp( now)
    timeString = datetime.datetime.strftime(ndt, "%X") 
    print "{0:s}.{1:03d} {2:7s} {3:s}".format(timeString, ndt.microsecond/1000, level, message)
    return


def swapBitOrder(inChar):
  # inChar is an input byte
  # outChar is the corresponding output byte

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


# check if the current message is a recent repeat of last message
eventTime = 0
eventMessage = 0
def isRecentRepeat(message, timeOfMessage):
  global eventTime
  global eventMessage

  if eventTime != 0: # not first pass, so repeat is possible
    if message == eventMessage:
      if timeOfMessage - eventTime < REPEAT_TIME: # part of the current burst
        return True
  eventTime = timeOfMessage # time of the first byte of the message
  eventMessage = message
  return False


def logReceiptW800message():
  global command
  global raw
  global intermediate
  global rearranged

  logMessage = ""
  # prepare logging message with raw inputs, as appropriate
  if loggingLevel <= loggingLevels['DEBUG 3']:
    for i in range (0,4):
      logMessage += "{0:02X} ".format( raw[i])
    logMessage += "> "
  if loggingLevel <= loggingLevels['DEBUG 2']:
    for i in range (0,4):
      logMessage += "{0:02X} ".format( intermediate[i])
    logMessage += "> "
  if loggingLevel <= loggingLevels['DEBUG 1']:
    for i in range (0,4):
      logMessage += "{0:02X} ".format( rearranged[i])
  logMessage += " " + command
  if logMessage is not "":
    log( "DEBUG 1", "Received w800 " + logMessage)


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
  global rearranged
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
    
    alias = unitMapping( unit) # returns X10 unit, alias or null string
    if alias is not '':
      subtopic = alias
    else:
      subtopic = NO_SUBTOPIC # flag to not send individual MQTT message
    command = unit + " " + commandCodePressed
  else:
    if rearranged[0] == 0x11:
      commandCodePressed = 'BRIGHT'
    elif rearranged[0] == 0x19:
      commandCodePressed = 'DIM'

    command = commandCodePressed
    alias = unitMapping( command)
    if alias is not '':
      subtopic = alias
    else:
      subtopic = NO_SUBTOPIC # flag to not send MQTT


def decodeX10security ():
  global rearranged
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
    alias = unitMapping( unit)
    if alias is not '':
      subtopic = alias
    else:
      subtopic = NO_SUBTOPIC # flag to not send MQTT
  else:
    # unknown X10 security device
    commandCodePressed = "UNKNOWN"
    unitNumber = "Unknown"
    subtopic = NO_SUBTOPIC # flag to not send MQTT



#
### MAIN
#
def main ():
  global portID
  global MQTT_mapString
  global MQTT_topic

  global now
  global raw
  global intermediate
  global rearranged

  # Read configuration file into parameters
  configure_parameters()
  convertMQTTmappingStringToList (MQTT_mapString)

  # Establish the connection on a specific port
  USBport = getPort( portID)
  ser = serial.Serial(USBport, 4800) 
  
  now = time.time()
  log ("INFO", "w800 Starting up...")
  
  while True:
    '''
    messages are each 4 bytes long
    these are read into the raw buffer
    when 4 bytes are available and received together, these are moved into the intermediate
    and rearraged into the rearranged buffer
    '''
    messageTime =  []
    raw =          [] 
    count = 0
    while count < 4:
      newByte = ord(ser.read()) # Read the newest output 
      raw.append( newByte)
      now = time.time()
      messageTime.append( now)
      log("DEBUG 4", "Read byte {0:02X} time delta {1:F}".format(newByte, now-messageTime[0]))
  
      count = count + 1
      if count == 4: # whole message has been received
        intermediate = []
        intermediate.append( swapBitOrder( raw[ 0]))
        intermediate.append( swapBitOrder( raw[ 1]))
        intermediate.append( swapBitOrder( raw[ 2]))
        intermediate.append( swapBitOrder( raw[ 3]))
  
        # change the byte order 0123 to 2301
        rearranged =   []
        rearranged.append( intermediate[ 2])
        rearranged.append( intermediate[ 3])
        rearranged.append( intermediate[ 0])
        rearranged.append( intermediate[ 1])
  
        #check if message transferred together
        raw_buffer = ""
        if now - messageTime[0] > acceptableMessageTimeWindow:
          while now - messageTime[0] > acceptableMessageTimeWindow and count > 0:
            #remember bytes for the error message
            raw_buffer += "{0:02X} ".format( raw[0])
            #discard the byte and its received time
            messageTime = messageTime[1:]
            raw = raw[1:]
            count = count - 1
          log( "WARNING", "Time disparity, discarding " + raw_buffer)
  
        #check if this is an X10 message
        elif rearranged[0] ^ rearranged[1] == 0xFF and rearranged[2] ^ rearranged[3] == 0xFF: # potentially X10 format
          if rearranged[0] & 0xE0 == 0:  # X10 format
            if not isRecentRepeat(rearranged[ 0] << 8 | rearranged[ 2], messageTime[0]):
              decodeX10message()
            else: # duplicate
              logReceiptW800message() 
              count = 0 # discard message
              messageTime =  []
              raw =          [] 
          else:
            log( "WARNING", "X10 message not recognized, discarding {0:02X} > {1:02X}".format(raw[0], intermediate[0]) )
            # toss the oldest byte and try again
            messageTime = messageTime[1:]
            raw = raw[1:]
            count = 3
  
        # check if this is an X10 PRO message
        elif rearranged[0] ^ rearranged[1] == 0xff and rearranged[2] ^ rearranged[3] == 0xf0: # is the X10 security format
          if not isRecentRepeat(rearranged[ 0] << 8 | rearranged[ 3], messageTime[0]):
            decodeX10security ()
          else: # duplicate
            logReceiptW800message() 
            count = 0 # discard message
            messageTime =  []
            raw =          [] 
  
        # handle unrecognized messages
        else:
          log( "WARNING", "message not recognized, discarding {0:02X} > {1:02X}".format(raw[0], intermediate[0]))
          # try this to fail more gracefully
          messageTime = messageTime[1:]
          raw = raw[1:]
          count = 3
        if count == 3:
          log ("DEBUG 1", "raw buffer: {0:02X} {1:02X} {2:02X}".format(raw[0], raw[1], raw[2]) )
    # process legitimate (though possibly duplicate) message
    logReceiptW800message() 
    if subtopic is not NO_SUBTOPIC:
      log( "INFO", "Sending MQTT message: /" + MQTT_topic + subtopic + ":" + commandCodePressed)
      publish.single(MQTT_topic + subtopic, commandCodePressed, hostname=MQTT_brokerHost)


main();

# put in stuff to allow this to be a module
# maybe should have done that with the MQTT stuff
