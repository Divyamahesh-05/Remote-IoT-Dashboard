#!/usr/bin/python
import sys
import os
import paho.mqtt.client as mqtt
from time import sleep, time
from datetime import datetime, timedelta
import json
import requests
import threading
import uuid
import schedule
from lora_tx_rx import lora
import subprocess

# give system time to settle
sleep(10)

# ---------------------
# Global state
# ---------------------
message_storage = []
old_payload = ""
Valve_array = []
group_array = []
publish_flag = False

# ---------------------
# MQTT callbacks
# ---------------------
def on_connect(client, userdata, flags, rc):
    global publish_flag
    print("Connected with result code", rc)
    if rc == 0:
        rt = datetime.now().strftime("%d/%m/%y %H:%M")
        print("Connected to MQTT Broker!", rt)
        publish_flag = True
    else:
        print("Failed to connect, return code %d" % rc)
    client.subscribe("end_time1")


def on_message(client, userdata, msg):
    rt = datetime.now().strftime("%d|%m|%y %H:%M:%S")
    payload = msg.payload
    # queue for processing in main loop to avoid doing heavy work in callback
    message_storage.append(payload)


# ---------------------
# Helper functions
# ---------------------
def count_motor_threads(motor_id):
    active_threads = list(threading._active.items())
    count = sum(1 for _, t in active_threads if motor_id in t.name)
    return count


def send_telegram(text, chat_ids=None):
    if chat_ids is None:
        chat_ids = ["**********", "************"]
    for chat_id in chat_ids:
        cmd = [
            "curl", "-s", "-X", "POST",
            "https://api.telegram.org/bot7645684070:............................/sendMessage",
            "-d", f"chat_id={chat_id}",
            "-d", f"text={text}"
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            print(f"Sent to {chat_id}: {result.stdout.strip()}")
            if result.stderr:
                print("telegram error:", result.stderr.strip())
        except Exception as e:
            print("Exception sending telegram:", e)


# ---------------------
# Valve -> publish end_time2 and cancel valve timer if exists
# ---------------------
def turn_off_valve(valve, command, state, end_time, motor_id):
    # Format end_time to "HH:MM" string if a datetime passed
    if isinstance(end_time, datetime):
        end_time_str = end_time.strftime("%H:%M")
    else:
        end_time_str = end_time

    msg = {
        "valve": valve,
        "command": command,
        "state": False,
        "end_time": end_time_str,
        "commandMeta": {
            "type": "valve",
            "valve": valve,
            "state": False,
            "end_time": end_time_str
        },
        "socketid": "**********",
        "_msgid": "***********",
        "motor_id": motor_id
    }

    client.publish("end_time2", json.dumps(msg))
    print("Published structured JSON to end_time2:", json.dumps(msg))

    # cancel any timer named for this valve
    Thread_tag = f"{motor_id};{valve};valve"
    for tid, thread in list(threading._active.items()):
        tag = thread.getName()
        if Thread_tag in tag:
            print("Found thread:", tag)
            try:
                if isinstance(thread, threading.Timer) and thread.is_alive():
                    thread.cancel()
                    print(Thread_tag, "is canceled")
                else:
                    print(Thread_tag, "is already finished or not a Timer")
            except Exception as e:
                print("Error canceling thread:", e)

    print("Threading count after valve off:", threading.active_count())
    print("Threads running after valve off:", list(threading._active.items()))


# ---------------------
# cyclic_start: open valves, publish, set stop-timer
# Parameters:
#   motor_id (str), valves (list of valve names), group_name (str),
#   end_time (str "HH:MM"), index (int)
# ---------------------
def cyclic_start(motor_id, valves, group_name, end_time, index):
    print("\n--- CYCLIC START ---")
    print("Motor:", motor_id, "Valves:", valves, "Group:", group_name, "end_time:", end_time)
    start_rx_success = True
    ranges = 3

    # open every valve with retries
    for valve in valves:
        valve_cmd = valve.lower().replace("valve", "v") + "_on"
        command = f"{motor_id};{valve_cmd};"
        print("Sending:", command)

        rec_v = 0
        valve_ok = False

        for attempt in range(3):
            print("Attempt:", attempt + 1)
            try:
                lora.start(command, ranges)
                sleep(0.5)
                rec = lora.on_rx_done(command)
                # guard StopIteration or generator issues
                try:
                    rec_v = rec.__next__()
                except StopIteration:
                    rec_v = 0
            except Exception as e:
                print("lora exception:", e)
                rec_v = 0

            if rec_v == 1:
                print("Valve", valve, "ON success")
                msg = f"{motor_id};{valve};{end_time};True"
                client.publish("group_msg", msg)
                sleep(1)
                valve_ok = True
                break
            else:
                print("Valve", valve, "failed attempt", attempt + 1)

        if not valve_ok:
            print("Valve", valve, "failed permanently")
            start_rx_success = False
            break

    # if all valves ON -> publish group on and set timer to stop
    if start_rx_success:
        group_payload = {
            "action": "toggle_status",
            "index": index,
            "groupName": group_name,
            "isOn": True,
            "motor_id": motor_id
        }
        group_msg = json.dumps(group_payload)
        print("Published group ON status:", group_msg)
        client.publish("grp_toggle", group_msg)

        message = f"{group_name} Group Successfully turned on"
        send_telegram(message)

        # compute stop time from end_time string
        now = datetime.now()
        try:
            stop_dt = datetime.strptime(end_time, "%H:%M").replace(
                year=now.year, month=now.month, day=now.day
            )
        except Exception as e:
            print("Error parsing end_time:", end_time, e)
            return

        if stop_dt <= now:
            stop_dt += timedelta(days=1)

        delay_seconds = (stop_dt - now).total_seconds()
        tag = f"{motor_id};{group_name};Cyclic"
        print("Timer tag:", tag, "Will stop after:", delay_seconds, "seconds")

        timer = threading.Timer(delay_seconds, cyclic_stop, args=[motor_id, valves, group_name, index])
        try:
            timer.setName(tag)
        except Exception:
            # setName may not exist in some Python versions; fallback:
            try:
                timer.name = tag
            except Exception:
                pass

        timer.start()
        print("Stop-timer started:", tag)
    else:
        message = f"{group_name} Group ON FAILURE"
        send_telegram(message)
        print("Group ON FAILED due to valve issues")


# ---------------------
# cyclic_stop: turn off motor + valves, publish, cancel timers
# Parameters:
#   motor_id (str), valves (list), group_name (str), index (int)
# ---------------------
def cyclic_stop(motor_id, valves, group_name, index):
    print("\n--- CYCLIC STOP ---")
    print("Motor:", motor_id, "Group:", group_name, "Valves:", valves)
    ranges = 3

    # determine motor off command
    off_cmd = "m1_off" if motor_id == "MGSTMAC0" else "m3_off"
    motor_command = f"{motor_id};{off_cmd};"
    motor_success = False

    # Only attempt motor-off if there are no other threads for this motor (optional behavior preserved)
    current_thread_count = count_motor_threads(motor_id)
    print("Motor-related active threads:", current_thread_count)
    if current_thread_count <= 1:
        for attempt in range(3):
            print("Motor OFF attempt:", attempt + 1)
            try:
                lora.start(motor_command, ranges)
                sleep(0.5)
                rec = lora.on_rx_done(motor_command)
                try:
                    rec_v = rec.__next__()
                except StopIteration:
                    rec_v = 0
            except Exception as e:
                print("lora exception (motor off):", e)
                rec_v = 0

            if rec_v == 1:
                print("Motor OFF success")
                client.publish("motor_off", f"{motor_id};{off_cmd}")
                motor_success = True
                sleep(1)
                break
            else:
                print("Motor OFF failed attempt", attempt + 1)
        if not motor_success:
            print("Motor OFF failed permanently")

    else:
        print("Motor has other active threads; skipping motor OFF command to avoid conflict")

    # Turn off every valve with retries
    all_valves_success = True
    for valve in valves:
        valve_cmd = valve.lower().replace("valve", "v") + "_off"
        command = f"{motor_id};{valve_cmd};"
        print("Sending valve OFF:", command)

        valve_ok = False
        for attempt in range(3):
            print("Valve OFF attempt:", attempt + 1)
            try:
                lora.start(command, ranges)
                sleep(0.5)
                rec = lora.on_rx_done(command)
                try:
                    rec_v = rec.__next__()
                except StopIteration:
                    rec_v = 0
            except Exception as e:
                print("lora exception (valve off):", e)
                rec_v = 0

            if rec_v == 1:
                print(valve, "OFF success")
                client.publish("group_msg", f"{motor_id};{valve};00:00;False")
                valve_ok = True
                sleep(1)
                break
            else:
                print(valve, "OFF failed attempt", attempt + 1)

        if not valve_ok:
            print(valve, "OFF failed permanently")
            all_valves_success = False
            break

    # If all valves succeeded, publish group OFF payload and cancel timer threads
    if all_valves_success:
        group_payload = {
            "action": "toggle_status",
            "index": index,
            "groupName": group_name,
            "isOn": False,
            "motor_id": motor_id
        }
        group_msg = json.dumps(group_payload)
        print("Published group OFF status:", group_msg)
        client.publish("grp_toggle", group_msg)

        message = f"{group_name} Group Successfully turned off"
        send_telegram(message)

        # Cancel timer threads with matching tag
        timer_tag = f"{motor_id};{group_name};Cyclic"
        print("Checking timers to cancel:", timer_tag)
        for tid, thread in list(threading._active.items()):
            try:
                name = thread.getName()
            except Exception:
                name = getattr(thread, "name", "")
            if timer_tag in name:
                print("Found timer thread:", name)
                try:
                    thread.cancel()
                    print("Timer cancelled:", name)
                except Exception as e:
                    print("Error cancelling timer:", e)

        print("Active threads now:", threading.active_count())
    else:
        message = f"{group_name} Group OFF FAILURE"
        send_telegram(message)
        print("Group OFF FAILED due to valve issues")


# ---------------------
# Store helpers used by timers
# ---------------------
def store(valve, command, state, end_time, motor_id):
    print("append_function ")
    value = (valve, command, state, end_time, motor_id)
    print("Value:", value)
    Valve_array.append(value)
    print("Valve_array:", Valve_array)


def grp_store(motor_id, valves, group_name, duration, index):
    print("append_function ")
    value = (motor_id, valves, group_name, duration, index)
    print("Value:", value)
    group_array.append(value)
    print("Group_array:", group_array)


# ---------------------
# restore_fun: schedule groups after reboot / bulk restore
# Accepts list of group dicts like before
# ---------------------
def restore_fun(groups):
    print("Restoring groups, count:", len(groups))
    for g in groups:
        try:
            group_name = g['groupName']
            start_time = g['startTime']          # "HH:MM"
            duration_minutes = int(g['duration'])  # minutes
            index = g.get('index', 0)
            valves = g.get('valves', [])
            motor_id = g.get('motor_id', "")
            days = g.get('days', [])

            # compute end_time as "HH:MM" string
            start_dt = datetime.strptime(start_time, "%H:%M")
            end_dt = start_dt + timedelta(minutes=duration_minutes)
            end_time = end_dt.strftime("%H:%M")

            cyclic_tag = f"{motor_id};{group_name}"
            print(f"Scheduling Group: {group_name} Motor: {motor_id} Start: {start_time} End: {end_time} Days: {days}")

            for day in days:
                method_name = day.lower()
                # validate method exists on schedule.every()
                if hasattr(schedule.every(), method_name):
                    try:
                        getattr(schedule.every(), method_name).at(start_time).do(
                            cyclic_start, motor_id, valves, group_name, end_time, index
                        ).tag(cyclic_tag)
                        print("Scheduled on", day)
                    except Exception as e:
                        print("Error scheduling on", day, e)
                else:
                    print("Unknown day name in schedule:", day)

            message = f"{group_name} Group RESTORED"
            # notify owner
            try:
                subprocess.run([
                    "curl", "-s", "-X", "POST",
                    "https://api.telegram.org/..................................",
                    "-d", "chat_id=.............",
                    "-d", f"text={message}"
                ], capture_output=True, text=True)
            except Exception as e:
                print("Telegram notify error:", e)

        except Exception as exc:
            print("Error restoring group:", exc)


# ---------------------
# Main message handler (incoming parsed dict)
# ---------------------
def main_function(msg):
    print("Processing message:", msg)
    # valve direct message
    if list(msg.keys())[0] == 'valve':
        valve = msg["valve"]
        command = msg["command"]
        state = msg["state"]
        end_time = msg["end_time"]
        motor_id = msg["motor_id"]
        now = datetime.now()

        # parse end_time into datetime for same day
        try:
            end_dt = datetime.strptime(end_time, "%H:%M").replace(
                year=now.year, month=now.month, day=now.day
            )
        except Exception as e:
            print("Invalid end_time format:", end_time, e)
            return

        remaining_seconds = int((end_dt - now).total_seconds())
        print("Valve message remaining_seconds:", remaining_seconds, msg)
        if remaining_seconds <= 0:
            print("End time already passed; skipping")
            return

        timer = threading.Timer(remaining_seconds, store, args=(valve, command, state, end_dt, motor_id))
        tag = f"{motor_id};{valve};valve"
        try:
            timer.setName(tag)
        except Exception:
            try:
                timer.name = tag
            except Exception:
                pass
        timer.start()

    # group actions
    elif "action" in msg:
        print("Group payload")
        action = msg.get("action")
        if action in ("save_group", "update_group"):
            group = msg['group']
            group_name = group['groupName']
            start_time = group['startTime']
            duration = int(group['duration'])
            start_dt = datetime.strptime(start_time, "%H:%M")
            end_dt = start_dt + timedelta(minutes=duration)
            end_time = end_dt.strftime("%H:%M")
            index = group['index']
            days = group['days']
            valves = group['valves']
            motor_id = group['motor_id']
            cyclic_tag = f"{motor_id};{group_name}"

            for day in days:
                method_name = day.lower()
                if hasattr(schedule.every(), method_name):
                    try:
                        getattr(schedule.every(), method_name).at(start_time).do(
                            cyclic_start, motor_id, valves, group_name, end_time, index
                        ).tag(cyclic_tag)
                    except Exception as e:
                        print("Error scheduling on", day, e)
                else:
                    print("Unknown day for scheduling:", day)

            print(f"Group '{group_name}' scheduled for days: {days} at {start_time}")

        elif action == "delete_group":
            group_name = msg['groupName']
            motor_id = msg['motor_id']
            cyclic_tag = f"{motor_id};{group_name}"
            print("Clearing schedule tag:", cyclic_tag)
            schedule.clear(cyclic_tag)

        elif action == "toggle_status":
            # original code queued a group stop/start via grp_store; preserve semantics
            group = msg['group']
            group_name = group['groupName']
            start_time = group['startTime']
            duration = int(group['duration'])
            start_dt = datetime.strptime(start_time, "%H:%M")
            end_dt = start_dt + timedelta(minutes=duration)
            end_time = end_dt.strftime("%H:%M")
            index = group['index']
            days = group.get('days', [])
            valves = group.get('valves', [])
            motor_id = group.get('motor_id', "")
            # queue for immediate processing (matching old behavior)
            grp_store(motor_id, valves, group_name, duration, index)

        else:
            print("Not a recognized action:", action)

    else:
        print("Unrecognized message format")


# ---------------------
# Program entrypoint
# ---------------------
try:
    if __name__ == "__main__":
        client = mqtt.Client("test3_05")
        client.on_connect = on_connect
        client.on_message = on_message
        print("client complete")
        client.connect("localhost", 1883, 60)
        client.loop_start()

        while True:
            schedule.run_pending()

            if publish_flag:
                client.publish("request", "")
                publish_flag = False
                print("Published request/group successfully")

            # process queued messages one-by-one
            if len(message_storage) > 0:
                if old_payload != message_storage[0]:
                    try:
                        raw = message_storage[0]
                        print("Raw incoming payload:", raw)
                        msg = json.loads(raw.decode('utf-8'))
                        if isinstance(msg, dict):
                            main_function(msg)
                        elif isinstance(msg, list) and isinstance(msg[0], dict):
                            restore_fun(msg)
                        old_payload = message_storage[0]
                        print("Current_thread:", threading.current_thread().getName())
                        print("threading_count", threading.active_count())
                        print("threading_items", list(threading._active.items()))
                        print("")
                    except Exception as e:
                        print("Exception while handling message:", e)
                else:
                    print("Removed Duplicate message")
                # consume the message
                message_storage.pop(0)
                print("Threading count:", threading.active_count())
                print("Threads running:", list(threading._active.items()))

            # process queued valve timers (from store)
            if len(Valve_array) > 0:
                print("Processing stored valve-off item")
                valve, command, state, end_time, motor_id = Valve_array[0]
                turn_off_valve(valve, command, state, end_time, motor_id)
                Valve_array.pop(0)

            # process queued group offs (from grp_store)
            elif len(group_array) > 0:
                print("Processing stored group-off item")
                motor_id, valves, group_name, duration, index = group_array[0]
                # call cyclic_stop immediately (preserve previous behavior)
                cyclic_stop(motor_id, valves, group_name, index)
                group_array.pop(0)

            sleep(0.2)

except KeyboardInterrupt:
    sys.stdout.flush()
    print("")
    sys.stderr.write("KeyboardInterrupt\n")
except ConnectionRefusedError:
    print("ConnectionRefusedError")