"""Stimulation hardware layer (shock trigger + scanner TTL through the Arduino).

The task only talks to the small interface of `Stimulator`:

    open()                    connect to the device (called from Task._setup)
    trigger(copy_channels)    fire one stimulation on STIM_CHANNEL, plus a
                              simultaneous copy on every channel in copy_channels
    pulse(channels)           low level: raise `channels` for `pulse_dur`
                              (use it for markers that are NOT a stimulation)
    poll_ttl()                number of scanner TTLs received since the last call
    all_off()                 force every channel low
    close()                   called by Task.unload() after every task
    shutdown()                really release the device (also run at Python exit)

To support other hardware, subclass `Stimulator` and implement `pulse()` (and
`poll_ttl()` if the device also receives the TTL).

Channel numbering follows the original loopback script: channel k is bit k of
the byte sent to the Arduino  (ch0 = 1 = pin 8 -> DS8R trigger via SPP,
ch1 = 2 = pin 9 -> PsychoPy sync marker, ch0+ch1 = 3).
Channels >= 2 only do something if the Arduino sketch maps those bits to pins.

SAFETY: opening the serial port RESETS most Arduino boards, and until the sketch
has restarted the pins are not driven. Keep the DS8R output disabled (or the
electrodes disconnected) until the "Connected to Arduino" message has been
printed, or put a pull-down resistor on the trigger line.
"""
import atexit
import sys
import time

from psychopy import core, event


class Stimulator:
    """Interface + shared behaviour. Subclasses implement `pulse()`."""

    STIM_CHANNEL = 0  # the channel wired to the stimulator trigger input
    pulse_dur = 0.010  # seconds the channel(s) stay high

    def open(self):
        pass

    def pulse(self, channels):
        raise NotImplementedError

    def trigger(self, copy_channels=()):
        """Fire one stimulation; the copies are raised in the same pulse."""
        self.pulse([self.STIM_CHANNEL, *copy_channels])

    def poll_ttl(self):
        """Return how many scanner TTLs arrived since the previous call."""
        return 0

    def all_off(self):
        pass

    def close(self):
        self.all_off()

    def shutdown(self):
        pass


class DryRunStimulator(Stimulator):
    """No hardware: prints what would be sent.

    Pressing a scanner-sync key (the keys listed in fmri.MR_settings["sync"],
    e.g. '5') emulates a TTL.
    """

    def pulse(self, channels):
        print(f"[DRY RUN] pulse on channels {sorted(set(channels))}")

    def poll_ttl(self):
        # imported here (not at the top) so that this file can still be run as a
        # plain script for the hardware self-test below
        from . import fmri, utils

        utils.poll_windows()
        return len(event.getKeys(fmri.MR_settings["sync"]))


class ArduinoDigitimer(Stimulator):
    """Digitimer DS8R driven through the Arduino (serial), same protocol as the
    standalone loopback script: write a bitmask byte, wait, write 0. The
    Arduino sends b'T' on serial for every scanner TTL it receives.

    The port is opened once (first task that needs it) and stays open between
    tasks, because every re-opening resets the Arduino. `close()` only drives
    the channels low; the port is released by `shutdown()` at Python exit.
    """

    def __init__(self, port="/dev/ttyACM0", baudrate=115200, pulse_dur=0.010):
        self.port, self.baudrate, self.pulse_dur = port, baudrate, pulse_dur
        self.arduino = None  # opened later, in open(), NOT at import time

    @staticmethod
    def channels_to_byte(channels):
        mask = 0
        for ch in channels:
            if not 0 <= ch <= 7:
                raise ValueError(f"Arduino channel must be in 0..7, got {ch}")
            mask |= 1 << ch
        return mask

    def open(self):
        if self.arduino is not None:
            return
        import serial

        try:
            self.arduino = serial.Serial(
                port=self.port, baudrate=self.baudrate, timeout=0.001
            )
        except Exception as e:
            raise RuntimeError(f"Could not open the Arduino on {self.port}: {e}") from e
        time.sleep(2)  # the Arduino resets when the serial port is opened
        self.arduino.reset_input_buffer()
        atexit.register(self.shutdown)
        print(f"[SUCCESS] Connected to Arduino on {self.port}")

    def pulse(self, channels):
        self.arduino.write(bytes([self.channels_to_byte(channels)]))  # HIGH
        core.wait(self.pulse_dur)
        self.arduino.write(bytes([0]))  # LOW

    def poll_ttl(self):
        waiting = self.arduino.in_waiting
        if not waiting:
            return 0
        return self.arduino.read(waiting).count(b"T")

    def all_off(self):
        if self.arduino is not None:
            self.arduino.write(bytes([0]))

    def shutdown(self):
        if self.arduino is not None:
            self.all_off()
            self.arduino.close()
            self.arduino = None


if __name__ == "__main__":
    # Hardware self-test, independent of the task architecture:
    #   python src/shared/stimulator.py /dev/ttyACM0
    # Fires 3 stimulations (ch0 + ch1 copy), 5 s apart, and prints any TTL seen.
    # Make sure the DS8R output is NOT connected to a participant.
    port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyACM0"
    stim = ArduinoDigitimer(port=port)
    stim.open()
    try:
        for i in range(3):
            stim.trigger(copy_channels=[1])
            print(f"stimulation {i + 1}/3 fired")
            t_end = core.getTime() + 5
            while core.getTime() < t_end:
                n = stim.poll_ttl()
                if n:
                    print(f"  TTL x{n}")
                core.wait(0.001)
    finally:
        stim.shutdown()
