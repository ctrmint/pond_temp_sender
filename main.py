# Mark Rodman
# ------------------------------------------------------
# Description
# Temperature measurement system, transmits results via
# UDP broadcast
# ------------------------------------------------------
import network, socket, struct, time, ubinascii
import dht, machine, onewire, ds18x20, ujson
from pico_hardware import OnboardTemp
from machine import Pin

# Configuration Constants
SSID = 'barpbarp6'
PASSWORD = '1qazxsw23edcvfr4'
MULTICAST_GROUP = '224.0.0.1'
MULTICAST_PORT = 5007
BROADCAST_IP = "10.81.1.255"
# PIN for I2C sensor(s)
SENSE_PIN = 16
# Onboard sensor ref
OPERATING_VOLTAGE = 3.3
BIT_RANGE = 65535
REF_TEMP = 27
# Example alarm thresholds - external sensors
LOW_ALARM_TEMP = 20.0  # Example low alarm threshold in Celsius
HIGH_ALARM_TEMP = 25.0  # Example high alarm threshold in Celsius
#
LOOP_SLEEP_SECS = 1
TYPE_HARDWARE = "Hardware"
led = Pin("LED", Pin.OUT)
led.off()

SENSOR_PLACEMENTS = {
    "28fd93df0d000054": "Pump housing",
    "2825d05704e13c71": "Pond",
    "287e9d5704e13ca2": "Pond"
}


def get_value_from_dict(dictionary, key):
    """
    Retrieve the value from a dictionary using the provided key.
    :param dictionary: The dictionary to search.
    :param key: The key to look up in the dictionary.
    :return: The value associated with the key, or None if the key is not found.
    """
    return dictionary.get(key, None)


def flasher(led, delay_ms, times):
    while times > 0:
        led.off()
        time.sleep_ms(delay_ms)
        led.on()
        times = times - 1
    return


# Configure WLAN
def connect_to_wlan(ssid, password):
    flasher(led, 5, 10)
    retry_count = 1
    time.sleep(3)
    # print("Attempting to connect to WiFi: " + SSID)
    wlan = network.WLAN(network.STA_IF)
    # time.sleep(5)
    wlan.active(True)
    time.sleep(2)
    wlan.connect(ssid, password)
    while not wlan.isconnected():
        flasher(led, 20, 20)
        # print("Retrying WiFi, retry "+ retry_count)
        retry_count += 1
        time.sleep(5)
    # print('____WLAN connected___')
    # print('IP address: ' + wlan.ifconfig()[0])
    # print('Netmask: ' + wlan.ifconfig()[1])
    # print('Gateway' + wlan.ifconfig()[2])
    return wlan


def external_sensors(roms, ds_sensor):
    """
    Get measurements from ds18x20 external sensors
    """
    measures = []
    for rom in roms:
        tempC = ds_sensor.read_temp(rom)

        if tempC < LOW_ALARM_TEMP or tempC > HIGH_ALARM_TEMP:
            alarm = True
        else:
            alarm = False

        measurement = {
            "type": TYPE_HARDWARE,
            "value": tempC,
            "sensor": rom_to_hex(rom),
            "location": str(get_value_from_dict(SENSOR_PLACEMENTS, rom_to_hex(rom))),
            "time": get_epoch_time(),
            "resolution_raw": get_resolution(ds_sensor, rom),
            "resolution_bits": (get_resolution(ds_sensor, rom)) * 9 + 9,
            "alarm": alarm
        }
        measures.append(measurement)
    return measures


# Function to get the current epoch time
def get_epoch_time():
    t = time.localtime()
    return time.mktime(t)


# Convert byte array to human-readable hex string
def rom_to_hex(rom):
    return ubinascii.hexlify(rom).decode('utf-8')


# Initialize the DS18X20 sensor
def init_sensor(pin_number):
    ds_pin = machine.Pin(pin_number)
    return ds18x20.DS18X20(onewire.OneWire(ds_pin))


# Function to get sensor resolution
def get_resolution(sensor, rom):
    return sensor.read_scratch(rom)[4] & 0x60 >> 5


def avg_from_json(json_array, key, condition_key, condition_val):
    """
    Calculates average value from JSON array.
    :json_array: JSON data structure (array), contains key value pairs from which the avg will be calculated
    :key: The key which contains the value to be averaged.
    :condition: The filter condition
    """
    average = 0
    # Filter the records for "Pond" and extract the celsius values
    values = [entry[key] for entry in json_array if entry.get(condition_key) == condition_val]
    # Calculate the average
    average = sum(values) / len(values) if values else 0
    return average


def main():
    time.sleep(5)
    led.on()
    roms = None
    c_max = None
    c_min = None
    summary = {}

    print("starting...")
    # Connect to WLAN
    wlan = connect_to_wlan(SSID, PASSWORD)
    # Create UDP Socket
    ttl = struct.pack('b', 1)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    # external sensor setup
    while not roms:
        ds_sensor = init_sensor(SENSE_PIN)
        roms = ds_sensor.scan()
        print('Found DS devices: ', roms)

    # onboard sensor setup
    print("Onboard sensor setup")
    sensor_temp = machine.ADC(4)
    conversion_factor = (OPERATING_VOLTAGE / BIT_RANGE)
    onboard_temp_sensor = OnboardTemp(name="Onboard Sensor", machine=(machine.ADC(4)), ref_temp=REF_TEMP,
                              bit_range=BIT_RANGE, operating_voltage=OPERATING_VOLTAGE)

    while wlan and socket and roms:
        try:
            led.off()
            # Get data from DS sensor
            ds_sensor.convert_temp()
            time.sleep_ms(750)
            measures = external_sensors(roms, ds_sensor)
            # Internal sensor
            onboard_temp_sensor.get_reading(verbose=False)
            
            measures.append({"location": "onboard", "type": TYPE_HARDWARE, "sensor": "default", "value": onboard_temp_sensor.current_temp, "max_c": onboard_temp_sensor.maximum, "min_C": onboard_temp_sensor.minimum, "time": get_epoch_time()})
 
            # Get average Pond temp from Pond results for each probe and append to JSON array
            average_pond_temp = avg_from_json(measures, "value", "location", "Pond")           
            measures.append({"location": "Pond", "value": average_pond_temp, "type": "avg_temp", "time": get_epoch_time()})
        
            # Perform max and min opertations and record result
            if c_max is None:
                c_max = average_pond_temp
                c_min = average_pond_temp
            else:
                if c_max < average_pond_temp:
                    c_max = average_pond_temp
                if c_min > average_pond_temp:
                    c_min = average_pond_temp
            
            # Add max and min values to JSON
            measures.append({"location": "Pond", "value": c_max, "type": "max", "time": get_epoch_time()})
            measures.append({"location": "Pond", "value": c_min, "type": "min", "time": get_epoch_time()})
            
            # Add Summary data to JSON
            summary = {"type": "summary", "location": "Pond", "avg": average_pond_temp, "min": c_min, "max": c_max}
            measures.append(summary)
            print(summary)
            
            # Convert JSON Object to String
            json_data = ujson.dumps(measures)

            led.on()
            # Send JSON Data via Broadcast UDP
            sock.sendto(json_data.encode(), (BROADCAST_IP, 5007))
        except:
            # Something errored, should really add error handling here.
            pass
            # print("something failed")

        # Wait for XX Seconds as defined by LOOP_SLEEP_SECS
        time.sleep(LOOP_SLEEP_SECS)


if __name__ == '__main__':
    main()
