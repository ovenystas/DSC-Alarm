#######################################################################
# DSC Alarm interface
# Originally developed by Travis Cook
# www.frightideas.com
#
# Redesign to replace Indigo with Fibaro Home Center 2 by Ove Nystås
#######################################################################

from datetime import datetime
import re
import serial  # installed with sudo apt-get install python3-serial
import time

ZONE_STATE_OPEN = 'open'
ZONE_STATE_CLOSED = 'closed'
ZONE_STATE_TRIPPED = 'tripped'
ZONE_GROUP_STATE_OPEN = 'zoneOpen'
ZONE_GROUP_STATE_CLOSED = 'allZonesClosed'
ZONE_GROUP_STATE_TRIPPED = 'zoneTripped'
ALARM_STATE_DISARMED = 'disarmed'
ALARM_STATE_EXIT_DELAY = 'exitDelay'
ALARM_STATE_FAILED_TO_ARM = 'FailedToArm'
ALARM_STATE_ARMED = 'armed'
ALARM_STATE_ENTRY_DELAY = 'entryDelay'
ALARM_STATE_TRIPPED = 'tripped'
KEYPAD_STATE_CHIME_ENABLED = 'enabled'
KEYPAD_STATE_CHIME_DISABLED = 'disabled'
ALARM_ARMED_STATE_DISARMED = 'disarmed'
ALARM_ARMED_STATE_STAY = 'stay'
ALARM_ARMED_STATE_AWAY = 'away'

LED_INDEX_LIST = ['None', 'Ready', 'Armed', 'Memory', 'Bypass', 'Trouble',
                 'Program', 'Fire', 'Backlight', 'AC']
LED_STATE_LIST = ['off', 'on', 'flashing']
ARMED_MODE_LIST = ['Away', 'Stay', 'Away, No Delay', 'Stay, No Delay']
PANIC_TYPE_LIST = ['None', 'Fire', 'Ambulance', 'Panic']
MONTH_LIST = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
              'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']

CMD_NORMAL = 0
CMD_THERMO_SET = 1
PING_INTERVAL = 301
HOLD_RETRY_TIME_MINUTES = 3

# Note the "indigo" module is automatically imported and made available inside
# our global name space by the host process.
###############################################################################


class Plugin(indigo.PluginBase):

    ########################################
    def __init__(self, pluginId, pluginDisplayName,
                 pluginVersion, pluginPrefs):
        self.States = self.enum(STARTUP=1, HOLD=2, HOLD_RETRY=3,
                                HOLD_RETRY_LOOP=4, BOTH_INIT=5,
                                ENABLE_TIME_BROADCAST=7,
                                BOTH_PING=8, BOTH_POLL=9)
        self.state = self.States.STARTUP
        self.logLevel = 1
        self.shutdown = False
        self.configRead = False
        self.interfaceState = 0
        self.zoneList = {}
        self.tempList = {}
        self.zoneGroupList = {}
        self.trippedZoneList = []
        self.triggerList = []
        self.keypadList = {}
        self.createVariables = False
        self.port = None
        self.repeatAlarmTripped = False
        self.isPortOpen = False
        self.txCmdList = []
        self.closeTheseZonesList = []
        self.currentHoldRetryTime = HOLD_RETRY_TIME_MINUTES
        self.ourVariableFolder = None
        self.configEmailUrgent = ""
        self.configEmailNotice = ""
        self.configEmailUrgentSubject = ""
        self.configEmailNoticeSubject = ""
        self.configSpeakVariable = None
        self.configKeepTimeSynced = True
        self.troubleCode = 0
        self.troubleClearedTimer = 0

    def enum(self, **enums):
        return type('Enum', (), enums)

    def __del__(self):
        indigo.PluginBase.__del__(self)

    ########################################
    def startup(self):
        self.logger.log(4, "startup called")
        self.configRead = self.getConfiguration(self.pluginPrefs)
        self.updater.checkVersionPoll()

    def shutdown(self):
        self.logger.log(4, "shutdown called")

    ###########################################################################
    # Indigo Device Start/Stop
    ###########################################################################
    def deviceStartComm(self, dev):
        self.logger.log(4, "<<-- entering deviceStartComm: %s (%d - %s)" %
                        (dev.name, dev.id, dev.deviceTypeId))

        props = dev.pluginProps

        if dev.deviceTypeId == 'alarmZoneGroup':
            if dev.id not in self.zoneGroupList:
                self.zoneGroupList[dev.id] = props['devList']

            if dev.states['state'] == 0:
                dev.updateStateOnServer(key="state",
                                        value=ZONE_GROUP_STATE_CLOSED)

        elif dev.deviceTypeId == 'alarmZone':
            if 'zoneNumber' not in props:
                return
            zone = int(props['zoneNumber'])
            if zone not in self.zoneList.keys():
                self.zoneList[zone] = dev.id
            else:
                self.logger.logError("Zone %s is already assigned "
                                     "to another device." % zone)

            # Check for new version zone states.
            # If they're not present tell Indigo to reread the Devices.xml file
            if 'LastChangedShort' not in dev.states:
                dev.stateListOrDisplayStateIdChanged()

            # If state is invalid or not there, set to closed
            if dev.states['state'] == 0:
                dev.updateStateOnServer(key='state',
                                        value=ZONE_STATE_CLOSED)

            dev.updateStateOnServer(key="LastChangedShort",
                                    value=self.
                                    getShortTime(dev.
                                                 states["LastChangedTimer"]))

            # Check for new version properties to see if we need to refresh
            # the device
            if 'occupancyGroup' not in props:
                self.logger.log(3, "Adding occupancyGroup to "
                                "device %s properties." % dev.name)
                props.update({"occupancyGroup": 0})
                dev.replacePluginPropsOnServer(props)

            # If the variable we used no longer exists then remove the varID
            if "var" in props:
                if props["var"] not in indigo.variables:
                    props["var"] = None
                    dev.replacePluginPropsOnServer(props)
            else:
                props["var"] = None
                dev.replacePluginPropsOnServer(props)

        elif dev.deviceTypeId == 'alarmKeypad':
            self.keypadList[int(dev.pluginProps['partitionNumber'])] = dev.id

            # self.logger.log(3, u"Adding keypad: %s" % self.keypadList)
            dev.updateStateOnServer(key='state',
                                    value=ALARM_STATE_DISARMED)

            # Check for new keypad states.
            # If they're not present tell Indigo to reread the Devices.xml file
            if 'ArmedState' not in dev.states:
                dev.stateListOrDisplayStateIdChanged()

        elif dev.deviceTypeId == 'alarmTemp':
            sensor = int(dev.pluginProps['sensorNumber'])
            if sensor not in self.tempList.keys():
                self.tempList[sensor] = dev

        self.logger.log(4, "exiting deviceStartComm -->>")

    def deviceStopComm(self, dev):
        self.logger.log(4, "<<-- entering deviceStopComm: %s (%d - %s)" %
                        (dev.name, dev.id, dev.deviceTypeId))

        if dev.deviceTypeId == 'alarmZoneGroup':
            if dev.id in self.zoneGroupList:
                del self.zoneGroupList[dev.id]

        elif dev.deviceTypeId == 'alarmZone':
            if 'zoneNumber' in dev.pluginProps:
                zone = int(dev.pluginProps['zoneNumber'])
                if zone in self.zoneList.keys():
                    del self.zoneList[zone]
                # self.logger.log(3, "ZoneList is now: %s" % self.zoneList)

        elif dev.deviceTypeId == 'alarmKeypad':
            if 'partitionNumber' in dev.pluginProps:
                keyp = int(dev.pluginProps['partitionNumber'])
                if keyp in self.keypadList:
                    del self.keypadList[keyp]

        elif dev.deviceTypeId == 'alarmTemp':
            if 'sensorNumber' in dev.pluginProps:
                tmp = int(dev.pluginProps['sensorNumber'])
                if tmp in self.tempList:
                    del self.tempList[int(dev.pluginProps['sensorNumber'])]

        self.logger.log(4, "exiting deviceStopComm -->>")

    # def deviceUpdated(self, origDev, newDev):
    #     self.logger.log(4, "<<-- entering deviceUpdated: %s" % origDev.name)
    #     origDev.name = newDev.name
    #     self.logger.log(4, "OrigDev now: %s" % origDev.name)
    #     self.DigiTemp.deviceStop(origDev)
    #     self.DigiTemp.deviceStart(newDev)

    ###########################################################################
    # Indigo Trigger Start/Stop
    ###########################################################################
    def triggerStartProcessing(self, trigger):
        self.logger.log(4, "<<-- entering triggerStartProcessing: %s (%d)" %
                        (trigger.name, trigger.id))
        self.triggerList.append(trigger.id)
        self.logger.log(4, "exiting triggerStartProcessing -->>")

    def triggerStopProcessing(self, trigger):
        self.logger.log(4, "<<-- entering triggerStopProcessing: %s (%d)" %
                        (trigger.name, trigger.id))
        if trigger.id in self.triggerList:
            self.logger.log(4, "TRIGGER FOUND")
            self.triggerList.remove(trigger.id)
        self.logger.log(4, "exiting triggerStopProcessing -->>")

    # def triggerUpdated(self, origDev, newDev):
    #     self.logger.log(4, "<<-- entering triggerUpdated: %s" % origDev.name)
    #     self.triggerStopProcessing(origDev)
    #     self.triggerStartProcessing(newDev)

    ###########################################################################
    # Indigo Trigger Firing
    ###########################################################################
    def triggerEvent(self, eventId):
        self.logger.log(4, "<<-- entering triggerEvent: %s " % eventId)
        for trigId in self.triggerList:
            trigger = indigo.triggers[trigId]
            if trigger.pluginTypeId == eventId:
                indigo.trigger.execute(trigger)
        return

    ###########################################################################
    # Indigo Menu Action Methods
    ###########################################################################
    def checkForUpdates(self):
        self.logger.log(1, "Manually checking for updates")
        self.updater.checkVersionNow()

    ###########################################################################
    # Indigo Action Methods
    ###########################################################################
    def methodDisarmAlarm(self, action):
        self.logger.log(1, "Disarming alarm")
        tx = "".join(["0401", self.pluginPrefs['code'],
                      "0" * (6 - len(self.pluginPrefs['code']))])
        self.txCmdList.append((CMD_NORMAL, tx))

    def methodArmStay(self, action):
        self.logger.log(1, "Arming alarm in stay mode.")
        self.txCmdList.append((CMD_NORMAL, '0311'))

    def methodArmAway(self, action):
        self.logger.log(1, "Arming alarm in away mode.")
        self.txCmdList.append((CMD_NORMAL, '0301'))

    def methodPanicAlarm(self, action):
        panicType = action.props['panicAlarmType']
        self.logger.log(1, "Activating Panic Alarm! (%s)" %
                        PANIC_TYPE_LIST[int(panicType)])
        self.txCmdList.append((CMD_NORMAL, '060' + panicType))

    def methodSendKeypress(self, action):
        self.logger.log(3, "Received Send Keypress Action")
        keys = action.props['keys']
        firstChar = True
        sendBreak = False
        for char in keys:
            if char == 'L':
                time.sleep(2)
                sendBreak = False

            if (firstChar is False):
                self.txCmdList.append((CMD_NORMAL, '070^'))

            if char != 'L':
                self.txCmdList.append((CMD_NORMAL, '070' + char))
                sendBreak = True

            firstChar = False
        if (sendBreak is True):
            self.txCmdList.append((CMD_NORMAL, '070^'))

    # Queue a command to set DSC Thermostat Setpoints
    def methodAdjustThermostat(self, action):
        self.logger.log(3, "Device %s:" % action)
        self.txCmdList.append((CMD_THERMO_SET, action))

    # The command queued above calls this routine to create the packet
    def setThermostat(self, action):
        # find this thermostat in our list to get the number
        for sensorNum in self.tempList.keys():
            if self.tempList[sensorNum].id == action.deviceId:
                break

        self.logger.log(3, "SensorNum = %s" % sensorNum)

        # send 095 for thermostat in question, wait for 563 response
        # self.logger.log(3, '095' + str(sensorNum))
        rx = self.sendPacket('095' + str(sensorNum), waitFor='563')
        if len(rx) == 0:
            self.logger.logError('Error getting current thermostat setpoints, '
                                 'aborting adjustment.')
            return

        if action.props['thermoAdjustmentType'] == '+' or \
                action.props['thermoAdjustmentType'] == '-':
            sp = 0
        else:
            sp = int(action.props['thermoSetPoint'])

        # then 096TC+000 to inc cool,
        #      096Th-000 to dec heat
        #      096Th=### to set setpoint
        # wait for 563 response
        # self.logger.log(3, '096%u%c%c%03u' %
        #                 (sensorNum, action.props['thermoAdjustWhich'],
        #                 action.props['thermoAdjustmentType'], sp))
        rx = self.sendPacket('096%u%c%c%03u' %
                             (sensorNum, action.props['thermoAdjustWhich'],
                              action.props['thermoAdjustmentType'], sp),
                             waitFor='563')
        if len(rx) == 0:
            self.logger.logError('Error changing thermostat setpoints, '
                                 'aborting adjustment.')
            return

        # send 097T
        # send 097 for thermostat in question to save setting,
        # wait for 563 response
        rx = self.sendPacket('097' + str(sensorNum), waitFor='563')
        if len(rx) == 0:
            self.logger.logError('Error saving thermostat setpoints, '
                                 'aborting adjustment.')
            return

    # Reset an Alarm Zone Group's timers to 0
    #
    def methodResetZoneGroupTimer(self, action):
        if action.deviceId in indigo.devices:
            zoneGrp = indigo.devices[action.deviceId]
            self.logger.log(3, "Manual timer reset for "
                               "alarm zone group \"%s\"" % zoneGrp.name)
            zoneGrp.updateStateOnServer(key="AnyMemberLastChangedTimer",
                                        value=0)
            zoneGrp.updateStateOnServer(key="EntireGroupLastChangedTimer",
                                        value=0)

    ###########################################################################
    # Indigo Pref UI Methods
    ###########################################################################

    # Validate the pluginConfig window after user hits OK
    # Returns False on failure, True on success
    def validatePrefsConfigUi(self, valuesDict):
        self.logger.log(3, "validating Prefs called")
        errorMsgDict = indigo.Dict()
        wasError = False

        if len(valuesDict['serialPort']) == 0:
            errorMsgDict['serialPort'] = "Select a valid serial port."
            wasError = True

        if len(valuesDict['code']) > 6:
            errorMsgDict['code'] = "The code must be 6 digits or less."
            wasError = True

        if len(valuesDict['code']) == 0:
            errorMsgDict['code'] = "You must enter the alarm's arm/disarm code."
            wasError = True

        if len(valuesDict['emailUrgent']) > 0:
            if not re.match(r"[^@]+@[^@]+\.[^@]+", valuesDict['emailUrgent']):
                errorMsgDict['emailUrgent'] = "Please enter a valid email address."
                wasError = True

        if wasError is True:
            return (False, valuesDict, errorMsgDict)

        # Tell DSC module to reread it's config
        self.configRead = False

        # User choices look good, so return True
        # (client will then close the dialog window).
        return (True, valuesDict)

    def validateActionConfigUi(self, valuesDict, typeId, actionId):
        self.logger.log(3, "validating Action Config called")
        if typeId == 'actionSendKeypress':
            keys = valuesDict['keys']
            cleanKeys = re.sub(r'[^a-e0-9LFAP<>=*#]+', '', keys)
            if len(keys) != len(cleanKeys):
                errorMsgDict['keys'] =
                "There are invalid keys in your keystring."
                return (False, valuesDict, errorMsgDict)
        return (True, valuesDict)

    def validateEventConfigUi(self, valuesDict, typeId, eventId):
        self.logger.log(3, "validating Event Config called")
        # self.logger.log(3, "Type: %s, Id: %s, Dict: %s" %
        # (typeId, eventId, valuesDict))
        if typeId == 'userArmed' or typeId == 'userDisarmed':
            code = valuesDict['userCode']
            if len(code) != 4:
                errorMsgDict['userCode'] =
                "The user code must be 4 digits in length."
                return (False, valuesDict, errorMsgDict)

            cleanCode = re.sub(r'[^0-9]+', '', code)
            if len(code) != len(cleanCode):
                errorMsgDict['userCode'] =
                "The code can only contain digits 0-9."
                return (False, valuesDict, errorMsgDict)
        return (True, valuesDict)

    def validateDeviceConfigUi(self, valuesDict, typeId, devId):
        self.logger.log(3, "validating Device Config called")
        # self.logger.log(3, "Type: %s, Id: %s, Dict: %s" %
        #                 (typeId, devId, valuesDict))
        if typeId == 'alarmZone':
            # zoneNum = int(valuesDict['zoneNumber'])
            if zoneNum in self.zoneList.keys() and
            devId != indigo.devices[self.zoneList[zoneNum]].id:
                # self.logger.log(3, "ZONEID: %s" % self.DSC.zoneList[zone].id)
                errorMsgDict = indigo.Dict()
                errorMsgDict['zoneNumber'] =
                "This zone has already been assigned to a different device."
                return (False, valuesDict, errorMsgDict)
        return (True, valuesDict)

    def getZoneList(self, filter_="", valuesDict=None,
                    typeId="", targetId=0):
        myArray = []
        for i in range(1, 65):
            zoneName = str(i)
            if i in self.zoneList.keys():
                zoneDev = indigo.devices[self.zoneList[i]]
                zoneName = ''.join([str(i), ' - ', zoneDev.name])
            myArray.append((str(i), zoneName))
        return myArray

    def getZoneDevices(self, filter_="", valuesDict=None,
                       typeId="", targetId=0):
        myArray = []
        for dev in indigo.devices:
            try:
                if dev.deviceTypeId == 'alarmZone':
                    myArray.append((dev.id, dev.name))
            except:
                pass
        return myArray

    ###########################################################################
    # Configuration Routines
    ###########################################################################

    # Reads the plugins config file into our own variables
    def getConfiguration(self, valuesDict):

        # Tell our logging class to reread the config for level changes
        self.logger.readConfig()

        self.logger.log(3, "getConfiguration start")

        try:
            # Get setting of Create Variables checkbox
            if valuesDict['createVariables'] is True:
                self.createVariables = True
            else:
                self.createVariables = False

            # If the variable folder doesn't exist disable variables,
            # we're done!
            if valuesDict['variableFolder'] not in indigo.variables.folders:
                self.createVariables = False

            self.configKeepTimeSynced = valuesDict.get('syncTime', True)

            self.configSpeakVariable = None
            if 'speakToVariableEnabled' in valuesDict:
                if valuesDict['speakToVariableEnabled'] is True:
                    self.configSpeakVariable =
                    int(valuesDict['speakToVariableId'])
                    if self.configSpeakVariable not in indigo.variables:
                        self.logger.logError('Speak variable not found in '
                                             'variable list')
                        self.configSpeakVariable = None

            self.configEmailUrgent = valuesDict.get('emailUrgent', '')
            self.configEmailNotice = valuesDict.get('updaterEmail', '')
            self.configEmailUrgentSubject =
            valuesDict.get('emailUrgentSubject', 'Alarm Tripped')
            self.configEmailNoticeSubject =
            valuesDict.get('updaterEmailSubject', 'Alarm Trouble')

            self.logger.log(3, "Configuration read successfully")
            return True

        except:
            self.logger.log(2, "Error reading plugin configuration. "
                            "(happens on very first launch)")
            return False

    ###########################################################################
    # Communication Routines
    ###########################################################################
    def calcChecksum(self, s):
        calcSum = 0
        for c in s:
            calcSum += ord(c)
        calcSum %= 256
        return calcSum

    def closePort(self):
        if self.port is None:
            return
        if self.port.isOpen() is True:
            self.port.close()
            self.port = None

    def openPort(self):
        self.closePort()
        self.logger.log(1, "Initializing communication on port %s" %
                        self.pluginPrefs['serialPort'])
        try:
            self.port = serial.Serial(self.pluginPrefs['serialPort'],
                                      9600,
                                      writeTimeout=1)
        except Exception, err:
            self.logger.logError('Error opening serial port: %s' %
                                 (str(err)))
            return False

        if self.port.isOpen() is True:
            self.port.flushInput()
            self.port.timeout = 1
            return True

        return False

    def readPort(self):
        if self.port.isOpen() is False:
            self.state = self.States.BOTH_INIT
            return ""
        data = ""
        try:
            data = self.port.readline()
        except Exception, err:
            self.logger.logError('Connection RX Error: %s' % (str(err)))
            # Return with '-' signaling calling subs to abort
            # so we can re-init.
            data = '-'
            # exit()
        except:
            self.logger.logError('Connection RX Problem, plugin quitting')
            exit()
        return data

    def writePort(self, data):
        self.port.write(data)

    def sendPacketOnly(self, data):
        pkt = "%s%02X\r\n" % (data, self.calcChecksum(data))
        self.logger.log(4, u"TX: %s" % pkt)
        try:
            self.writePort(pkt)
        except Exception, err:
            self.logger.logError('Connection TX Error: %s' % (str(err)))
            exit()
        except:
            self.logger.logError('Connection TX Problem, plugin quitting')
            exit()

    def sendPacket(self, tx, waitFor='500', rxTimeout=3, txRetries=3):
        retries = txRetries
        txCmd = tx[:3]

        while txRetries > 0:
            self.sendPacketOnly(tx)
            ourTimeout = time.time() + rxTimeout
            txRetries -= 1
            while time.time() < ourTimeout:
                if self.shutdown is True:
                    return ''
                (rxCmd, rxData) = self.readPacket()

                # If rxCmd == - then the socket closed, return for re-init
                if rxCmd == '-':
                    return '-'

                if rxCmd == '502':
                    self.logger.logError('Received system error after '
                                         'sending command, aborting.')
                    return ''

                # If rxCmd is not 0 length then we received a response
                if len(rxCmd) > 0:
                    if waitFor == '500':
                        if (rxCmd == '500') and (rxData == txCmd):
                            return rxData
                    elif (rxCmd == waitFor):
                        return rxData
            if txCmd != '000':
                self.logger.logError('Timed out after waiting for response to '
                                     'command %s for %u seconds, retrying.' %
                                     (tx, rxTimeout))
        self.logger.logError('Resent command %s %u times with no success, '
                             'aborting.' % (tx, retries))
        return ''

    def readPacket(self):
        data = self.readPort()
        if len(data) == 0:
            return ('', '')
        elif data == '-':
            # socket has closed, return with signal to re-initialize
            return ('-', '')

        data = data.strip()

        m = re.search(r'^(...)(.*)(..)$', data)
        if not m:
            return ('', '')

        # Put this try in to try to catch exceptions when non-ascii characters
        # were received, not sure why they are being received.
        try:
            self.logger.log(4, "RX: %s" % data)
            (cmd, dat, sum_) = (m.group(1), m.group(2), int(m.group(3), 16))
        except:
            self.logger.logError('IT-100 Error: '
                                 'Received a response with invalid characters')
            return ('', '')

        if sum_ != self.calcChecksum("".join([cmd, dat])):
            self.logger.logError("Checksum did not match "
                                 "on a received packet.")
            return ('', '')

        # Parse responses based on cmd value
        #
        if cmd == '500':
            self.logger.log(3, "ACK for cmd %s." % dat)
            self.cmdAck = dat

        elif cmd == '501':
            self.logger.logError('IT-100: '
                                 'Received a command with a bad checksum')

        elif cmd == '502':
            errText = 'Unknown'

            if dat == '001':
                errText = 'Receive Buffer Overrun (a command is received '
                'while another is still being processed)'
            elif dat == '002':
                errText = 'Receive Buffer Overflow'
            elif dat == '003':
                errText = 'Transmit Buffer Overflow'

            elif dat == '010':
                errText = 'Keybus Transmit Buffer Overrun'
            elif dat == '011':
                errText = 'Keybus Transmit Time Timeout'
            elif dat == '012':
                errText = 'Keybus Transmit Mode Timeout'
            elif dat == '013':
                errText = 'Keybus Transmit Keystring Timeout'
            elif dat == '014':
                errText = 'Keybus Interface Not Functioning '
                '(the TPI cannot communicate with the security system)'
            elif dat == '015':
                errText = 'Keybus Busy '
                '(Attempting to Disarm or Arm with user code)'
            elif dat == '016':
                errText = 'Keybus Busy – Lockout '
                '(The panel is currently in Keypad Lockout – '
                'too many disarm attempts)'
            elif dat == '017':
                errText = 'Keybus Busy – Installers Mode '
                '(Panel is in installers mode, most functions are unavailable)'
            elif dat == '018':
                errText = 'Keybus Busy – General Busy '
                '(The requested partition is busy)'

            elif dat == '020':
                errText = 'API Command Syntax Error'
            elif dat == '021':
                errText = 'API Command Partition Error '
                '(Requested Partition is out of bounds)'
            elif dat == '022':
                errText = 'API Command Not Supported'
            elif dat == '023':
                errText = 'API System Not Armed '
                '(sent in response to a disarm command)'
            elif dat == '024':
                errText = 'API System Not Ready to Arm '
                '(not secure, in delay, or already armed)'
                self.triggerEvent('eventFailToArm')
                self.speak('speakTextFailedToArm')
            elif dat == '025':
                errText = 'API Command Invalid Length'
            elif dat == '026':
                errText = 'API User Code not Required'
            elif dat == '027':
                errText = 'API Invalid Characters in Command'

            self.logger.logError("IT-100 Error (%s): %s" % (dat, errText))

        elif cmd == '505':
            if dat == '3':
                self.logger.log(3, 'Received login request')

        elif cmd == '510':
            # Keypad LED State Update
            leds = int(dat, 16)

            if leds & 1 > 0:
                self.updateKeypad(0, 'LEDReady', 'on')
            else:
                self.updateKeypad(0, 'LEDReady', 'off')

            if leds & 2 > 0:
                self.updateKeypad(0, 'LEDArmed', 'on')
            else:
                self.updateKeypad(0, 'LEDArmed', 'off')

            if leds & 16 > 0:
                self.updateKeypad(0, 'LEDTrouble', 'on')
            else:
                self.updateKeypad(0, 'LEDTrouble', 'off')

        elif cmd == '511':
            # Keypad LED Flashing State Update
            # Same as 510 above but means an LED is flashing
            # We don't use this right now
            pass

        elif cmd == '550':

            m = re.search(r'^(\d\d)(\d\d)(\d\d)(\d\d)(\d\d)$', dat)
            if m:
                tHour = int(m.group(1))
                tMin = int(m.group(2))
                dMonth = int(m.group(3)) - 1
                dMonthDay = int(m.group(4))
                dYear = int(m.group(5))

                # Check if we should sync time
                if self.configKeepTimeSynced is True:
                    d = datetime.now()
                    # Is the hour different or minute off by
                    # more than one minute?
                    if (d.hour != tHour) or (abs(d.minute - tMin) > 1):
                        self.logger.log(1,
                                        "Setting alarm panel time and date.")
                        self.txCmdList.append((CMD_NORMAL, "010%s" %
                                               d.strftime("%H%M%m%d%y")))
                    else:
                        self.logger.log(3, "Alarm time is within 1 minute of "
                                        "actual time, no update necessary.")

        elif cmd == '561' or cmd == '562':
            m = re.search(r'^(.)(...)$', dat)
            if m:
                (sensor, temp) = (int(m.group(1)), int(m.group(2)))
                if cmd == '562':
                    self.updateSensorTemp(sensor, 'outside', temp)
                else:
                    self.updateSensorTemp(sensor, 'inside', temp)

        elif cmd == '563':
            m = re.search(r'^(.)(...)(...)$', dat)
            if m:
                (sensor, cool, heat) = (int(m.group(1)),
                                        int(m.group(2)),
                                        int(m.group(3)))
                self.updateSensorTemp(sensor, 'cool', cool)
                self.updateSensorTemp(sensor, 'heat', heat)

        elif cmd == '601':
            m = re.search(r'^(.)(...)$', dat)
            if m:
                (partition, zone) = (int(m.group(1)), int(m.group(2)))
                self.updateZoneState(zone, ZONE_STATE_TRIPPED)
                if zone not in self.trippedZoneList:
                    self.trippedZoneList.append(zone)
                    self.sendZoneTrippedEmail()

        elif cmd == '602':
            m = re.search(r'^(.)(...)$', dat)
            if m:
                (partition, zone) = (int(m.group(1)), int(m.group(2)))
                self.logger.log(1, "Zone %d Restored. (Partition %d)" %
                                (zone, partition))

        elif cmd == '609':
            zone = int(dat)
            self.logger.log(3, "Zone number %d Open." % zone)
            self.updateZoneState(zone, ZONE_STATE_OPEN)
            if self.repeatAlarmTripped is True:
                if zone in self.closeTheseZonesList:
                    self.closeTheseZonesList.remove(zone)

        elif cmd == '610':
            zone = int(dat)
            self.logger.log(3, "Zone number %d Closed." % zone)
            # Update the zone to closed ONLY if the alarm is not tripped
            # We want the tripped states to be preserved so someone looking
            # at their control page will see all the zones that have been
            # opened since the break in.
            if self.repeatAlarmTripped is False:
                self.updateZoneState(zone, ZONE_STATE_CLOSED)
            else:
                self.closeTheseZonesList.append(zone)

        elif cmd == '620':
            self.logger.log(1, "Duress Alarm Detected")

        elif cmd == '621':
            self.logger.log(1, "Fire Key Alarm Detected")

        elif cmd == '623':
            self.logger.log(1, "Auxiliary Key Alarm Detected")

        elif cmd == '625':
            self.logger.log(1, "Panic Key Alarm Detected")

        elif cmd == '631':
            self.logger.log(1, "Auxiliary Input Alarm Detected")

        elif cmd == '632':
            self.logger.log(1, "Auxiliary Input Alarm Restored")

        elif cmd == '650':
            self.logger.log(3, "Partition %d Ready" % int(dat))

        elif cmd == '651':
            self.logger.log(3, "Partition %d Not Ready" % int(dat))

        elif cmd == '652':
            if len(dat) == 1:
                partition = int(dat)
                self.logger.log(3, "Alarm Armed. (Partition %d)" % partition)
                self.updateKeypad(partition, 'state', ALARM_STATE_ARMED)
                # TODO: This response does not tell us armed type trigger.
                #       Stay, Away, etc.  :(
            elif len(dat) == 2:
                m = re.search(r'^(.)(.)$', dat)
                if m:
                    (partition, mode) = (int(m.group(1)), int(m.group(2)))
                    self.logger.log(1,
                                    "Alarm Armed in %s mode. (Partition %d)" %
                                    (ARMED_MODE_LIST[mode], partition))
                    if (mode == 0) or (mode == 2):
                        armedEvent = 'armedAway'
                        self.updateKeypad(partition,
                                          'ArmedState',
                                          ALARM_ARMED_STATE_AWAY)
                    else:
                        armedEvent = 'armedStay'
                        self.updateKeypad(partition,
                                          'ArmedState',
                                          ALARM_ARMED_STATE_STAY)

                    self.triggerEvent(armedEvent)
                    self.updateKeypad(partition,
                                      'state',
                                      ALARM_STATE_ARMED)

        elif cmd == '653':
            # Partition Ready - Forced Arming Enabled
            # We don't do anything with this now.
            pass

        elif cmd == '654':
            self.logger.log(1, "Alarm TRIPPED! (Partition %d)" % int(dat))
            self.updateKeypad(int(dat),
                              'state',
                              ALARM_STATE_TRIPPED)
            self.triggerEvent('eventAlarmTripped')
            self.repeatAlarmTrippedNext = time.time()
            self.repeatAlarmTripped = True

        elif cmd == '655':
            # If the alarm has been disarmed while it was tripped,
            # update any zone state that were closed during the break in.
            # We don't update them during the event so that Indigo's zone
            # states will represent a zone as tripped during the entire event.
            if self.repeatAlarmTripped is True:
                self.repeatAlarmTripped = False
                for zone in self.closeTheseZonesList:
                    self.updateZoneState(zone, ZONE_STATE_CLOSED)
                self.closeTheseZonesList = []

            partition = int(dat)
            self.logger.log(1, "Alarm Disarmed. (Partition %d)" % partition)
            self.trippedZoneList = []
            self.updateKeypad(partition,
                              'state',
                              ALARM_STATE_DISARMED)
            self.updateKeypad(partition,
                              'ArmedState', ALARM_ARMED_STATE_DISARMED)
            self.triggerEvent('eventAlarmDisarmed')
            self.speak('speakTextDisarmed')

        elif cmd == '656':
            self.logger.log(1, "Exit Delay. (Partition %d)" % int(dat))
            self.updateKeypad(int(dat), 'state', ALARM_STATE_EXIT_DELAY)
            self.speak('speakTextArming')

        elif cmd == '657':
            self.logger.log(1, "Entry Delay. (Partition %d)" % int(dat))
            self.updateKeypad(int(dat), 'state', ALARM_STATE_ENTRY_DELAY)
            self.speak('speakTextEntryDelay')

        elif cmd == '663':
            partition = int(dat)
            self.logger.log(1, "Keypad Chime Enabled. (Partition %d)" %
                            partition)
            self.updateKeypad(partition,
                              'KeypadChime',
                              KEYPAD_STATE_CHIME_ENABLED)

        elif cmd == '664':
            partition = int(dat)
            self.logger.log(1, "Keypad Chime Disabled. (Partition %d)" %
                            partition)
            self.updateKeypad(partition,
                              'KeypadChime',
                              KEYPAD_STATE_CHIME_DISABLED)

        elif cmd == '672':
            self.logger.log(1, "Alarm Failed to Arm. (Partition %d)" %
                            int(dat))
            self.triggerEvent('eventFailToArm')
            self.speak('speakTextFailedToArm')
        elif cmd == '673':
            self.logger.log(3, "Partition %d Busy." % int(dat))
        elif cmd == '700' or cmd == '701' or cmd == '702':
            m = re.search(r'^(.)(....)$', dat)
            if m:
                (partition, user) = (int(m.group(1)), m.group(2))
                self.logger.log(1, "Alarm armed by user %s. (Partition %d)" %
                                (user, partition))
                for trig in self.triggerList:
                    trigger = indigo.triggers[trig]
                    if trigger.pluginTypeId == 'userArmed':
                        if trigger.pluginProps['userCode'] == user:
                            indigo.trigger.execute(trigger.id)
        elif cmd == '750':
            m = re.search(r'^(.)(....)$', dat)
            if m:
                (partition, user) = (int(m.group(1)), m.group(2))
                self.logger.log(1,
                                "Alarm disarmed by user %s. (Partition %d)" %
                                (user, partition))
                for trig in self.triggerList:
                    trigger = indigo.triggers[trig]
                    if trigger.pluginTypeId == 'userDisarmed':
                        if trigger.pluginProps['userCode'] == user:
                            indigo.trigger.execute(trigger.id)

        elif cmd == '800':
            self.logger.log(1, "Alarm panel battery is low.")
            self.sendTroubleEmail("Alarm panel battery is low.")

        elif cmd == '801':
            self.logger.log(1, "Alarm panel battery is now ok.")
            self.sendTroubleEmail("Alarm panel battery is now ok.")

        elif cmd == '802':
            self.logger.log(1, "AC Power Lost.")
            self.sendTroubleEmail("AC Power Lost.")
            self.triggerEvent('eventNoticeAC_Trouble')

        elif cmd == '803':
            self.logger.log(1, "AC Power Restored.")
            self.sendTroubleEmail("AC Power Restored.")
            self.triggerEvent('eventNoticeAC_Restore')

        elif cmd == '806':
            self.logger.log(1, "An open circuit has been detected across "
                               "the bell terminals.")
            self.sendTroubleEmail("An open circuit has been detected across "
                                  "the bell terminals.")

        elif cmd == '807':
            self.logger.log(1, "The bell circuit has been restored.")
            self.sendTroubleEmail("The bell circuit has been restored.")

        elif cmd == '840':
            self.logger.log(1, "Trouble Status (LED ON). (Partition %d)" %
                            int(dat))
            self.troubleClearedTimer = 0

        elif cmd == '841':
            self.logger.log(2, "Trouble Status Restore (LED OFF). "
                            "(Partition %d)" % int(dat))
            if self.troubleCode > 0:
                # If the trouble light goes off, set a 10 second timer.
                # If the light is still off after 10 seconds we'll clear our
                # status- This is required because the panel turns the light
                # off/on quickly when the light is actually on.
                self.troubleClearedTimer = 10

        elif cmd == '849':
            self.logger.log(3, "Recevied trouble code byte 0x%s" % dat)
            newCode = int(dat, 16)

            if newCode != self.troubleCode:
                self.troubleCode = newCode
                if self.troubleCode > 0:
                    body = "Trouble Code Received:\n"
                    if self.troubleCode & 1:
                        body += "- Service is Required\n"
                    if self.troubleCode & 2:
                        body += "- AC Power Lost\n"
                    if self.troubleCode & 4:
                        body += "- Telephone Line Fault\n"
                    if self.troubleCode & 8:
                        body += "- Failure to Communicate\n"
                    if self.troubleCode & 16:
                        body += "- Sensor/Zone Fault\n"
                    if self.troubleCode & 32:
                        body += "- Sensor/Zone Tamper\n"
                    if self.troubleCode & 64:
                        body += "- Sensor/Zone Low Battery\n"
                    if self.troubleCode & 128:
                        body += "- Loss of Time\n"
                    self.sendTroubleEmail(body)

        elif cmd == '851':
            self.logger.log(3, "Partition Busy Restore. (Partition %d)" %
                            int(dat))
        elif cmd == '896':
            self.logger.log(3, "Keybus Fault")
        elif cmd == '897':
            self.logger.log(3, "Keybus Fault Restore")
        elif cmd == '900':
            self.logger.logError("Code Required")

        elif cmd == '901':
            # for char in dat:
            #     self.logger.log(3, u"LCD DEBUG: %d" % ord(char))
            m = re.search(r'^...(..)(.*)$', dat)
            if m:
                lcdText = re.sub(r'[^ a-zA-Z0-9_/\:-]+', ' ', m.group(2))
                half = len(lcdText) / 2
                half1 = lcdText[:half]
                half2 = lcdText[half:]
                self.logger.log(3, "LCD Update, Line 1:'%s' Line 2:'%s'" %
                                (half1, half2))
                self.updateKeypad(0, 'LCDLine1', half1)
                self.updateKeypad(0, 'LCDLine2', half2)

        elif cmd == '903':
            m = re.search(r'^(.)(.)$', dat)
            if m:
                (ledName, ledState) = (LED_INDEX_LIST[int(m.group(1))],
                                       LED_STATE_LIST[int(m.group(2))])
                self.logger.log(3, "LED '%s' is '%s'." % (ledName, ledState))

                if ledState == 'flashing':
                    ledState = 'on'
                if ledName == 'Ready':
                    self.updateKeypad(0, 'LEDReady', ledState)
                elif ledName == 'Armed':
                    self.updateKeypad(0, 'LEDArmed', ledState)
                elif ledName == 'Trouble':
                    self.updateKeypad(0, 'LEDTrouble', ledState)

        elif cmd == '904':
            self.logger.log(3, "Beep Status")

        elif cmd == '905':
            self.logger.log(3, "Tone Status")

        elif cmd == '906':
            self.logger.log(3, "Buzzer Status")

        elif cmd == '907':
            self.logger.log(3, "Door Chime Status")

        elif cmd == '908':
            m = re.search(r'^(..)(..)(..)$', dat)
            if m:
                self.logger.log(3, "DSC Software Version %s.%s" %
                                (m.group(1), m.group(2)))
        else:
            # self.logger.log(3, "RX: %s" % data)
            self.logger.log(2, "Unrecognized command received "
                            "(Cmd:%s Dat:%s Sum:%d)" % (cmd, dat, sum_))

        return (cmd, dat)

    ###########################################################################
    # Indigo Device State Updating
    ###########################################################################

    # Updates temperature of DSC temperature sensor
    def updateSensorTemp(self, sensorNum, key, temp):
        if temp > 127:
            temp = 127 - temp
        self.logger.log(3, "Temp sensor %d %s temp now %d degrees." %
                        (sensorNum, key, temp))
        if sensorNum in self.tempList.keys():
            if key == 'inside':
                self.tempList[sensorNum]. \
                    updateStateOnServer(key="temperatureInside", value=temp)
            elif key == 'outside':
                self.tempList[sensorNum]. \
                    updateStateOnServer(key="temperatureOutside", value=temp)
            elif key == 'cool':
                self.tempList[sensorNum]. \
                    updateStateOnServer(key="setPointCool", value=temp)
            elif key == 'heat':
                self.tempList[sensorNum]. \
                    updateStateOnServer(key="setPointHeat", value=temp)

            if self.tempList[sensorNum].pluginProps['zoneLogChanges'] == 1:
                self.logger.log(1, "Temp sensor %d %s temp now %d degrees." %
                                (sensorNum, key, temp))

    # Updates zone group
    def updateZoneGroup(self, zoneGroupDevId):
        zoneGrp = indigo.devices[zoneGroupDevId]
        zoneGrp.updateStateOnServer(key="AnyMemberLastChangedTimer", value=0)
        newState = ZONE_GROUP_STATE_CLOSED
        for zoneId in self.zoneGroupList[zoneGroupDevId]:
            zoneState = indigo.devices[int(zoneId)].states['state']
            if (zoneState != ZONE_STATE_CLOSED) and
                    (newState != ZONE_GROUP_STATE_TRIPPED):
                if zoneState == ZONE_STATE_OPEN:
                    newState = ZONE_GROUP_STATE_OPEN
                elif zoneState == ZONE_STATE_TRIPPED:
                    newState = ZONE_GROUP_STATE_TRIPPED

        if zoneGrp.states['state'] != newState:
            zoneGrp.updateStateOnServer(key="EntireGroupLastChangedTimer",
                                        value=0)
            zoneGrp.updateStateOnServer(key="state",
                                        value=newState)

    # Updates indigo variable instance var with new value varValue
    def updateZoneState(self, zoneKey, newState):

        if zoneKey in self.zoneList.keys():
            zone = indigo.devices[self.zoneList[zoneKey]]
            # zoneType = zone.pluginProps['zoneType']

            # If the new state is different from the old state
            # then lets update timers and set the new state
            if zone.states['state'] != newState:
                # This is a new state, update all states and timers
                zone.updateStateOnServer(key="LastChangedShort", value="0m")
                zone.updateStateOnServer(key="LastChangedTimer", value=0)
                zone.updateStateOnServer(key="state", value=newState)

                # Check if this zone is assigned to a zone group
                # so we can update it
                for devId in self.zoneGroupList:
                    # self.logger.log(3, "Zone Group id: %s contains %s" %
                    #                 (zone.id,self.zoneGroupList[devId]))
                    if str(zone.id) in self.zoneGroupList[devId]:
                        self.updateZoneGroup(devId)

                if 'var' in zone.pluginProps.keys():
                    self.updateVariable(zone.pluginProps['var'], newState)

                if newState == ZONE_STATE_TRIPPED:
                    self.logger.log(1, "Alarm Zone '%s' TRIPPED!" % zone.name)

                if zone.pluginProps['zoneLogChanges'] == 1:
                    if newState == ZONE_STATE_OPEN:
                        self.logger.log(1, "Alarm Zone '%s' Opened." %
                                        zone.name)
                    elif newState == ZONE_STATE_CLOSED:
                        self.logger.log(1, "Alarm Zone '%s' Closed." %
                                        zone.name)

    def updateKeypad(self, partition, stateName, newState):

        self.logger.log(4, "Updating state %s for keypad "
                        "on partition %u to %s." %
                        (stateName, partition, newState))

        # If we're updating the main keypad state, update the variable too
        if stateName == 'state':
            self.updateVariable(self.pluginPrefs['variableState'], newState)

        if partition == 0:
            for keyk in self.keypadList.keys():
                keyp = indigo.devices[self.keypadList[keyk]]
                keyp.updateStateOnServer(key=stateName, value=newState)
            return

        if partition in self.keypadList.keys():
            keyp = indigo.devices[self.keypadList[partition]]
            keyp.updateStateOnServer(key=stateName, value=newState)

    ###########################################################################
    # Misc
    ###########################################################################
    def sendZoneTrippedEmail(self):

        if (len(self.configEmailUrgent) == 0) or
        (len(self.trippedZoneList) == 0):
            return

        theBody = "The following zone(s) have been tripped:\n\n"

        for zoneNum in self.trippedZoneList:

            if zoneNum in self.closeTheseZonesList:
                stateNow = "closed"
            else:
                stateNow = "open"

            zone = indigo.devices[self.zoneList[zoneNum]]

            theBody += "%s (currently %s)\n" % (zone.name, stateNow)

        theBody += "\n--\nDSC Alarm Plugin\n\n"

        self.logger.log(1, "Sending zone tripped email to %s." %
                        self.configEmailUrgent)

        contentPrefix = self.pluginPrefs.get('emailUrgentContent', '')
        if len(contentPrefix) > 0:
            theBody = contentPrefix + "\n\n" + theBody

        indigo.server.sendEmailTo(self.configEmailUrgent,
                                  subject=self.configEmailUrgentSubject,
                                  body=theBody)

    def sendTroubleEmail(self, bodyText):
        if len(self.configEmailNotice) == 0:
            return

        self.logger.log(1, "Sending trouble email to %s." %
                        self.configEmailNotice)

        contentPrefix = self.pluginPrefs.get('updaterEmailContent', '')
        if len(contentPrefix) > 0:
            bodyText = contentPrefix + "\n\n" + bodyText

        indigo.server.sendEmailTo(self.configEmailNotice,
                                  subject=self.configEmailNoticeSubject,
                                  body=bodyText)

    def sayThis(self, text):
        self.logger.log(3, "SAY: %s" % text)
        if self.configSpeakVariable is not None:
            if self.configSpeakVariable in indigo.variables:
                indigo.variable.updateValue(self.configSpeakVariable,
                                            value=text)
        else:
            indigo.server.speak(text)

    def speak(self, textId):
        self.logger.log(3, "ID: %s" % textId)
        if self.pluginPrefs['speakingEnabled'] is False:
            return

        if len(self.pluginPrefs[textId]) == 0:
            return

        if textId == 'speakTextFailedToArm':
            zones = 0
            zoneText = ''
            for zoneNum in self.zoneList.keys():
                zone = indigo.devices[self.zoneList[zoneNum]]
                if zone.states['state.open'] is True:
                    if zones > 0:
                        zoneText += ', '
                    zoneText += zone.name.replace("Alarm_", "")
                    zones += 1

            if zones == 0:
                say = self.pluginPrefs[textId]
            if zones == 1:
                say = self.pluginPrefs[textId] +
                '  The ' + zoneText + ' is open.'
            else:
                say = self.pluginPrefs[textId] +
                '  The following zones are open: ' + zoneText + '.'

            self.sayThis(say)

        elif textId == 'speakTextTripped':
            zones = 0
            zoneText = ''
            for zoneNum in self.trippedZoneList:
                zone = indigo.devices[self.zoneList[zoneNum]]
                if zones > 0:
                    zoneText += ', '
                zoneText += zone.name.replace("Alarm_", "")
                zones += 1
            if zones == 1:
                say = self.pluginPrefs[textId] +
                '  The ' + zoneText + ' has been tripped.'
            else:
                say = self.pluginPrefs[textId] +
                '  The following zones have been tripped: ' + zoneText + '.'
            self.sayThis(say)
        else:
            self.sayThis(self.pluginPrefs[textId])

    # Updates indigo variable instance var with new value varValue
    def updateVariable(self, varID, varValue):
        if self.createVariables is False:
            return
        # self.logger.log(3, u"Variable: %s" % varID)
        if varID is None:
            return
        if varID in indigo.variables:
            indigo.variable.updateValue(varID, value=varValue)

    # Converts given time in minutes to a human format
    # 3m, 5h, 2d, etc.
    def getShortTime(self, minutes):
        # If time is less than an hour then show XXm
        if minutes < 60:
            return str(minutes) + 'm'
        # If it's less than one day then show XXh
        elif minutes < 1440:
            return str(int(minutes / 60)) + 'h'
        # If it's less than one hundred days then show XXd
        elif minutes < 43200:
            return str(int(minutes / 1440)) + 'd'
        # If it's anything more than one hundred days then show nothing
        else:
            return ''

    ###########################################################################
    # Concurrent Thread
    ###########################################################################
    def runConcurrentThread(self):
        self.logger.log(3, "runConcurrentThread called")
        self.minuteTracker = time.time() + 60
        self.nextUpdateCheckTime = 0

        # While Indigo hasn't told us to shutdown
        while self.shutdown is False:

            self.timeNow = time.time()

            if self.state == self.States.STARTUP:
                self.logger.log(3, "STATE: Startup")

                if self.configRead is False:
                    if self.getConfiguration(self.pluginPrefs) is True:
                        self.configRead = True

                if self.configRead is True:
                    self.state = self.States.BOTH_INIT

                self.sleep(1)

            elif self.state == self.States.HOLD:
                if self.configRead is False:
                    self.state = self.States.STARTUP
                self.sleep(1)

            elif self.state == self.States.HOLD_RETRY:
                self.logger.log(1, "Plugin will attempt to re-initialize "
                                "again in %u minutes." %
                                self.currentHoldRetryTime)
                self.nextRetryTime =
                    self.timeNow + (HOLD_RETRY_TIME_MINUTES * 60)
                self.state = self.States.HOLD_RETRY_LOOP

            elif self.state == self.States.HOLD_RETRY_LOOP:
                if self.configRead is False:
                    self.state = self.States.STARTUP
                if self.timeNow >= self.nextRetryTime:
                    self.state = self.States.BOTH_INIT
                self.sleep(1)

            elif self.state == self.States.BOTH_INIT:
                if self.openPort() is True:
                    self.state = self.States.ENABLE_TIME_BROADCAST
                else:
                    self.logger.logError('Error opening port, will retry in "
                                         "%u minutes.' %
                                         self.currentHoldRetryTime)
                    self.state = self.States.HOLD_RETRY

            elif self.state == self.States.ENABLE_TIME_BROADCAST:
                # Enable time broadcast
                self.logger.log(2, "Enabling Time Broadcast")
                rx = self.sendPacket('0561')
                if len(rx) > 0:
                    self.logger.log(2, "Time Broadcast enabled.")
                    self.state = self.States.BOTH_PING
                else:
                    self.logger.logError('Error enabling Time Broadcast.')
                    self.state = self.States.HOLD_RETRY

            elif self.state == self.States.BOTH_PING:
                # Ping the panel to confirm we are in communication
                err = True
                self.logger.log(2, "Pinging the panel to test "
                                "communication...")
                rx = self.sendPacket('000')
                if len(rx) > 0:
                    self.logger.log(2, "Ping was successful.")
                    err = False
                else:
                    self.logger.logError('Error pinging panel, aborting.')

                if err is True:
                    self.state = self.States.HOLD_RETRY
                else:
                    # Request a full state update
                    self.logger.log(2, "Requesting a full state update.")
                    rx = self.sendPacket('001')
                    if len(rx) == 0:
                        self.logger.logError('Error getting state update.')
                        self.state = self.States.HOLD_RETRY
                    else:
                        self.logger.log(2, "State update request successful, "
                                        "initialization complete, "
                                        "starting normal operation.")
                        self.state = self.States.BOTH_POLL

            elif self.state == self.States.BOTH_POLL:
                if self.configRead is False:
                    self.state = self.States.STARTUP
                else:
                    if len(self.txCmdList) > 0:
                        (cmdType, data) = self.txCmdList[0]
                        if cmdType == CMD_NORMAL:
                            txRsp = self.sendPacket(data)
                            if txRsp == '-':
                                # If we receive - socket has closed,
                                # lets re-init
                                self.logger.logError('Tried to send data but '
                                                     'socket seems to have '
                                                     'closed.  Trying to '
                                                     're-initialize.')
                                self.state = self.States.BOTH_INIT
                            else:
                                # send was a success, remove command from queue
                                del self.txCmdList[0]

                        elif cmdType == CMD_THERMO_SET:
                            self.setThermostat(data)
                    else:
                        (rxRsp, rxData) = self.readPacket()
                        if rxRsp == '-':
                            # If we receive - socket has closed, lets re-init
                            self.logger.logError('Tried to read data but '
                                                 'socket seems to have '
                                                 'closed. Trying to '
                                                 're-initialize.')
                            self.state = self.States.BOTH_INIT

            # Check if the trouble timer counter is timing
            # We need to know if the trouble light has remained off
            # for a few seconds before we assume the trouble is cleared
            if self.troubleClearedTimer > 0:
                self.troubleClearedTimer -= 1
                if self.troubleClearedTimer == 0:
                    self.troubleCode = 0
                    self.sendTroubleEmail("Trouble Code Cleared")

            if self.repeatAlarmTripped is True:
                # timeNow = time.time()
                if self.timeNow >= self.repeatAlarmTrippedNext:
                    self.repeatAlarmTrippedNext = self.timeNow + 12
                    self.speak('speakTextTripped')

            # If a minute has elapsed
            if self.timeNow >= self.minuteTracker:

                # Do we need to check for a new version?
                self.updater.checkVersionPoll()

                # Increment all zone changed timers
                self.minuteTracker += 60
                for zoneKey in self.zoneList.keys():
                    zone = indigo.devices[self.zoneList[zoneKey]]
                    tmr = zone.states["LastChangedTimer"] + 1
                    zone.updateStateOnServer(key="LastChangedTimer",
                                             value=tmr)
                    zone.updateStateOnServer(key="LastChangedShort",
                                             value=self.getShortTime(tmr))

                for zoneGroupDeviceId in self.zoneGroupList:
                    zoneGroupDevice = indigo.devices[zoneGroupDeviceId]
                    tmr = zoneGroupDevice. \
                        states["AnyMemberLastChangedTimer"] + 1
                    zoneGroupDevice. \
                        updateStateOnServer(key="AnyMemberLastChangedTimer",
                                            value=tmr)
                    tmr = zoneGroupDevice. \
                        states["EntireGroupLastChangedTimer"] + 1
                    zoneGroupDevice. \
                        updateStateOnServer(key="EntireGroupLastChangedTimer",
                                            value=tmr)

        self.closePort()
        self.logger.log(3, "Exiting Concurrent Thread")

    def stopConcurrentThread(self):
        self.logger.log(3, "stopConcurrentThread called")
        self.shutdown = True
        self.logger.log(3, "Exiting stopConcurrentThread")
