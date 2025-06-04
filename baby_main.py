import RPi.GPIO as GPIO
import time
import random
import threading
import csv # Import the csv module for logging

# --- Configuration ---
# GPIO Pin assignments (BCM numbering)
BUTTON_HUNGER_PIN = 17  # GPIO pin for the 'Hunger' button
BUTTON_DIAPER_PIN = 27  # GPIO pin for the 'Diaper' button

# Required press-and-hold durations (in seconds)
HOLD_DURATION_HUNGER = 3 * 60  # 3 minutes for feeding
HOLD_DURATION_DIAPER = 1 * 60  # 1 minute for diaper change

# Newborn baby schedule simulation (in seconds)
# These are approximate and can be adjusted for difficulty
NEWBORN_SLEEP_DURATION_MIN = 2 * 60 * 60  # Minimum 2 hours of sleep
NEWBORN_SLEEP_DURATION_MAX = 4 * 60 * 60  # Maximum 4 hours of sleep
NEWBORN_HUNGER_INTERVAL_MIN = 2 * 60 * 60  # Baby gets hungry every 2-3 hours
NEWBORN_HUNGER_INTERVAL_MAX = 3 * 60 * 60
NEWBORN_DIAPER_INTERVAL_MIN = 1 * 60 * 60  # Baby needs diaper change every 1-3 hours
NEWBORN_DIAPER_INTERVAL_MAX = 3 * 60 * 60

# --- CSV Log File Configuration ---
LOG_FILE = "baby_doll_activity.csv"
CSV_HEADERS = [
    "Timestamp",        # When the event occurred (YYYY-MM-DD HH:MM:SS)
    "Event Type",       # e.g., "Baby State Change", "Need Met", "Button Press"
    "Baby State",       # Current state of the baby (e.g., "SLEEPING", "HUNGRY")
    "Need Type",        # Specific need (e.g., "Hunger", "Diaper")
    "Time to Tend (s)", # Time taken to address a need (in seconds)
    "Button Pin",       # GPIO pin of the button involved
    "Hold Duration (s)",# How long a button was held (in seconds)
    "Message"           # Descriptive message about the event
]

# --- Global Variables for State Management ---
current_baby_state = "SLEEPING"
last_fed_time = time.time()
last_diaper_change_time = time.time()
last_sleep_start_time = time.time()
need_start_time = None  # When the current need (hunger/diaper) started

# Button press tracking
button_press_start_times = {
    BUTTON_HUNGER_PIN: None,
    BUTTON_DIAPER_PIN: None
}
button_hold_timers = {
    BUTTON_HUNGER_PIN: None,
    BUTTON_DIAPER_PIN: None
}

# Threading locks to prevent race conditions
state_lock = threading.Lock()
button_lock = threading.Lock()

# --- Logging Function ---
def log_activity(event_type, baby_state=None, need_type=None, time_to_tend=None,
                 button_pin=None, hold_duration=None, message=""):
    """
    Logs an activity to the CSV file.

    Args:
        event_type (str): Category of the event (e.g., "Baby State Change", "Need Met").
        baby_state (str, optional): The baby's state at the time of the event. Defaults to current_baby_state.
        need_type (str, optional): The specific need involved (e.g., "Hunger", "Diaper").
        time_to_tend (int, optional): Time taken to address a need in seconds.
        button_pin (int, optional): The GPIO pin number of the button involved.
        hold_duration (int, optional): How long the button was held in seconds.
        message (str, optional): A descriptive message for the log entry.
    """
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    row = [
        timestamp,
        event_type,
        baby_state if baby_state is not None else current_baby_state, # Use provided state or global
        need_type,
        time_to_tend,
        button_pin,
        hold_duration,
        message
    ]

    # Check if the file exists to decide whether to write headers
    file_exists = False
    try:
        with open(LOG_FILE, 'r') as f:
            file_exists = True
    except FileNotFoundError:
        pass # File does not exist, will be created

    with open(LOG_FILE, 'a', newline='') as f: # Open in append mode, newline='' prevents blank rows
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(CSV_HEADERS) # Write headers only if the file is new
        writer.writerow(row)

# --- GPIO Setup ---
def setup_gpio():
    GPIO.setmode(GPIO.BCM)  # Use BCM numbering for GPIO pins

    # Setup buttons as input with pull-up resistors
    # Pull-up resistor means the pin is HIGH by default, goes LOW when pressed
    GPIO.setup(BUTTON_HUNGER_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(BUTTON_DIAPER_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    # Add event detection for button presses (falling edge - button goes LOW)
    GPIO.add_event_detect(BUTTON_HUNGER_PIN, GPIO.FALLING, callback=button_pressed_callback, bouncetime=200)
    GPIO.add_event_detect(BUTTON_DIAPER_PIN, GPIO.FALLING, callback=button_pressed_callback, bouncetime=200)

    # Add event detection for button releases (rising edge - button goes HIGH)
    GPIO.add_event_detect(BUTTON_HUNGER_PIN, GPIO.RISING, callback=button_released_callback, bouncetime=200)
    GPIO.add_event_detect(BUTTON_DIAPER_PIN, GPIO.RISING, callback=button_released_callback, bouncetime=200)

    print("GPIO setup complete. Waiting for baby needs...")
    log_activity("System Start", message="GPIO setup complete.")

# --- Button Callbacks ---
def button_pressed_callback(channel):
    """Callback function when a button is pressed (falling edge)."""
    with button_lock:
        button_press_start_times[channel] = time.time()
        msg = f"Button {channel} pressed."
        print(f"\n--- {msg} at {time.strftime('%H:%M:%S')} ---")
        log_activity("Button Event", button_pin=channel, message=msg)

        # Determine required hold duration for this button
        if channel == BUTTON_HUNGER_PIN:
            required_hold = HOLD_DURATION_HUNGER
        elif channel == BUTTON_DIAPER_PIN:
            required_hold = HOLD_DURATION_DIAPER
        else:
            return # Not a recognized button

        # Cancel any existing timer for this button to prevent multiple timers
        if button_hold_timers[channel] is not None:
            button_hold_timers[channel].cancel()

        # Start a new timer that will call check_button_hold after 'required_hold' seconds
        # This timer will only trigger if the button is *still* held down after the duration
        button_hold_timers[channel] = threading.Timer(required_hold, check_button_hold, args=(channel,))
        button_hold_timers[channel].start()

def button_released_callback(channel):
    """Callback function when a button is released (rising edge)."""
    with button_lock:
        press_start = button_press_start_times[channel]
        if press_start is not None:
            hold_duration = time.time() - press_start
            msg = f"Button {channel} released. Held for {int(hold_duration)} seconds."
            print(f"--- {msg} ---")
            log_activity("Button Event", button_pin=channel, hold_duration=int(hold_duration), message=msg)

            # Cancel the hold timer immediately if the button is released before the required duration
            if button_hold_timers[channel] is not None:
                button_hold_timers[channel].cancel()
                button_hold_timers[channel] = None # Clear the timer reference

            # Reset the press start time
            button_press_start_times[channel] = None

            # Check if the action was successful based on hold duration
            required_duration = HOLD_DURATION_HUNGER if channel == BUTTON_HUNGER_PIN else HOLD_DURATION_DIAPER
            if hold_duration < required_duration:
                fail_msg = f"Hold duration too short for button {channel}. Required: {required_duration}s."
                print(fail_msg)
                log_activity("Action Failed", button_pin=channel, message=fail_msg)
        else:
            msg = f"Button {channel} released but no press start time recorded (might be a bounce)."
            print(msg)
            log_activity("Button Event", button_pin=channel, message=msg)

def check_button_hold(channel):
    """
    This function is called by the threading.Timer if the button is held for the required duration.
    It means the button is still pressed after the required time.
    """
    with button_lock:
        # Verify the button is still pressed (LOW indicates pressed with PUD_UP)
        if GPIO.input(channel) == GPIO.LOW:
            process_button_action(channel)
        else:
            # This case can happen if the button was released just as the timer was about to fire.
            # The button_released_callback would have already handled it.
            msg = f"Button {channel} hold timer triggered, but button was already released."
            print(msg)
            log_activity("Button Event", button_pin=channel, message=msg)
        button_hold_timers[channel] = None # Clear the timer reference after processing

def process_button_action(channel):
    """Processes the action if a button was held for the required duration and the need matches."""
    global current_baby_state, last_fed_time, last_diaper_change_time, last_sleep_start_time, need_start_time

    with state_lock:
        if channel == BUTTON_HUNGER_PIN:
            if current_baby_state == "HUNGRY":
                time_to_tend = time.time() - need_start_time
                msg = f"BABY FED! Time to tend: {int(time_to_tend)} seconds."
                print(f"\n*** {msg} ***")
                log_activity("Need Met", baby_state="FED", need_type="Hunger", time_to_tend=int(time_to_tend), message=msg)

                last_fed_time = time.time()
                last_sleep_start_time = time.time() # Baby goes to sleep after feeding
                current_baby_state = "SLEEPING"
                log_activity("Baby State Change", baby_state="SLEEPING", message="Baby went to sleep after feeding.")
                need_start_time = None # Reset need start time
            else:
                msg = "Baby is not hungry right now. Keep an eye on the needs!"
                print(f"\n{msg}")
                log_activity("Incorrect Action", need_type="Hunger", message=msg)
        elif channel == BUTTON_DIAPER_PIN:
            if current_baby_state == "WET_DIAPER":
                time_to_tend = time.time() - need_start_time
                msg = f"DIAPER CHANGED! Time to tend: {int(time_to_tend)} seconds."
                print(f"\n*** {msg} ***")
                log_activity("Need Met", baby_state="DRY", need_type="Diaper", time_to_tend=int(time_to_tend), message=msg)

                last_diaper_change_time = time.time()
                last_sleep_start_time = time.time() # Baby goes to sleep after diaper change
                current_baby_state = "SLEEPING"
                log_activity("Baby State Change", baby_state="SLEEPING", message="Baby went to sleep after diaper change.")
                need_start_time = None # Reset need start time
            else:
                msg = "Diaper is not wet right now. Check again later!"
                print(f"\n{msg}")
                log_activity("Incorrect Action", need_type="Diaper", message=msg)

# --- Baby State Logic ---
def check_baby_needs():
    """Determines the baby's current need based on time and updates state."""
    global current_baby_state, need_start_time

    with state_lock:
        current_time = time.time()

        if current_baby_state == "SLEEPING":
            elapsed_sleep_time = current_time - last_sleep_start_time
            if elapsed_sleep_time >= random.uniform(NEWBORN_SLEEP_DURATION_MIN, NEWBORN_SLEEP_DURATION_MAX):
                msg = f"Baby waking up after {int(elapsed_sleep_time)} seconds of sleep!"
                print(f"\n--- {msg} ---")
                log_activity("Baby State Change", baby_state="AWAKE", message=msg)

                # Randomly decide the next need (Hunger or Wet Diaper)
                if random.random() < 0.5: # 50% chance to be hungry first
                    current_baby_state = "HUNGRY"
                    msg = "BABY IS HUNGRY! (Press and hold Hunger button for 3 minutes)"
                    print(f"!!! {msg} !!!")
                    log_activity("Baby State Change", baby_state="HUNGRY", need_type="Hunger", message=msg)
                else:
                    current_baby_state = "WET_DIAPER"
                    msg = "BABY HAS A WET DIAPER! (Press and hold Diaper button for 1 minute)"
                    print(f"!!! {msg} !!!")
                    log_activity("Baby State Change", baby_state="WET_DIAPER", need_type="Diaper", message=msg)
                need_start_time = current_time # Record when the need started
            # else:
                # No need to log every second while sleeping peacefully, keeps log cleaner.

        elif current_baby_state == "HUNGRY":
            # Log periodic reminders if hungry and not attended to
            if need_start_time is not None:
                elapsed_since_hungry = current_time - need_start_time
                # Log every minute the baby is hungry
                if int(elapsed_since_hungry) > 0 and int(elapsed_since_hungry) % 60 == 0:
                    msg = f"Baby is still hungry! Elapsed: {int(elapsed_since_hungry)} seconds."
                    print(msg)
                    log_activity("Need Unmet", baby_state="HUNGRY", need_type="Hunger", message=msg)

        elif current_baby_state == "WET_DIAPER":
            # Log periodic reminders if diaper is wet and not attended to
            if need_start_time is not None:
                elapsed_since_wet = current_time - need_start_time
                # Log every 30 seconds the diaper is wet
                if int(elapsed_since_wet) > 0 and int(elapsed_since_wet) % 30 == 0:
                    msg = f"Baby still has a wet diaper! Elapsed: {int(elapsed_since_wet)} seconds."
                    print(msg)
                    log_activity("Need Unmet", baby_state="WET_DIAPER", need_type="Diaper", message=msg)

        # Also check for needs while awake, even if not the primary state
        # This allows for multiple needs to develop if not attended to quickly
        if current_baby_state != "SLEEPING":
            # Check for hunger development
            if current_time - last_fed_time >= random.uniform(NEWBORN_HUNGER_INTERVAL_MIN, NEWBORN_HUNGER_INTERVAL_MAX):
                if current_baby_state != "HUNGRY": # Only change state if not already hungry
                    msg = "Baby is getting hungry again!"
                    print(f"\n!!! {msg} !!!")
                    log_activity("Baby State Change", baby_state="HUNGRY", need_type="Hunger", message=msg)
                    current_baby_state = "HUNGRY"
                    if need_start_time is None: need_start_time = current_time # Set need start if this is the first need

            # Check for diaper change development
            if current_time - last_diaper_change_time >= random.uniform(NEWBORN_DIAPER_INTERVAL_MIN, NEWBORN_DIAPER_INTERVAL_MAX):
                if current_baby_state != "WET_DIAPER": # Only change state if not already wet
                    msg = "Baby needs a diaper change again!"
                    print(f"\n!!! {msg} !!!")
                    log_activity("Baby State Change", baby_state="WET_DIAPER", need_type="Diaper", message=msg)
                    current_baby_state = "WET_DIAPER"
                    if need_start_time is None: need_start_time = current_time # Set need start if this is the first need


# --- Main Loop ---
def main():
    setup_gpio()
    print("Baby Doll Simulator Started!")
    print(f"Log file: {LOG_FILE}")
    print(f"Hunger button: GPIO {BUTTON_HUNGER_PIN} (Hold for {HOLD_DURATION_HUNGER/60} minutes)")
    print(f"Diaper button: GPIO {BUTTON_DIAPER_PIN} (Hold for {HOLD_DURATION_DIAPER/60} minute)")
    print("Initial state: SLEEPING")
    log_activity("System Info", message="Baby Doll Simulator application started.")
    log_activity("Initial State", baby_state="SLEEPING", message="Baby is initially sleeping.")


    try:
        while True:
            check_baby_needs()
            time.sleep(1) # Check needs every second to ensure responsiveness
    except KeyboardInterrupt:
        print("\nExiting Baby Doll Simulator.")
        log_activity("System Stop", message="Application stopped by user (KeyboardInterrupt).")
    finally:
        GPIO.cleanup() # Clean up GPIO settings on exit to release pins

if __name__ == "__main__":
    main()
