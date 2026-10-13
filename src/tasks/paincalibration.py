import time
import pandas as pd
from psychopy import visual, core, logging

from .task_base import Task
from ..shared import config, utils
from ..shared.eyetracking import fixation_dot

try:
    import serial
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False


class PainCalibration(Task):
    """
    Nociceptive Pain Calibration Task.
    Integrates fMRI trigger waiting ('T' on Arduino Pin 8), delivers TTL triggers 
    via custom Arduino USB board, and outputs BIDS-compliant event logs.
    """

    DEFAULT_INSTRUCTION = """
Vous allez recevoir une série de stimulation douloureuse.
Veuillez garder vos yeux fixés sur la croix au centre de l'écran.
"""

    def __init__(
        self,
        n_stim=5,
        isi=6.0,
        target_channel=0,  # Board channel label 0 to 7 (Channel 0 = byte 1)
        pulse_duration=0.010,  # 10 ms trigger pulse
        arduino_port="/dev/ttyACM0",
        baudrate=115200, 
        initial_wait=4.0,
        final_wait=4.0,
        fmri=False,  # Inherited or passed via parsed args
        *args,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.n_stim = int(n_stim)
        self.isi = float(isi)
        self.target_channel = int(target_channel)
        self.pulse_duration = float(pulse_duration)
        self.arduino_port = arduino_port
        self.baudrate = baudrate
        self.initial_wait = float(initial_wait)
        self.final_wait = float(final_wait)
        self.fmri = fmri

        # Channel 0 -> byte 1, Ch 1 -> byte 2, Ch 2 -> byte 4...
        self.trigger_byte = 1 << self.target_channel

        self.serial_dev = None
        self.events_log = []

    def _setup(self, exp_win):
        # 1. Visual stimuli
        self.fixation = fixation_dot(exp_win)
        self.instruction_stim = visual.TextStim(
            exp_win,
            text=self.instruction,
            alignText="center",
            color="white",
            wrapWidth=config.WRAP_WIDTH,
        )
        self.waiting_stim = visual.TextStim(
            exp_win,
            text="Waiting for scanner...",
            alignText="center",
            color="white",
            wrapWidth=config.WRAP_WIDTH,
        )

        # 2. Hardware Trigger Serial Connection
        if HAS_SERIAL:
            try:
                self.serial_dev = serial.Serial(
                    port=self.arduino_port,
                    baudrate=self.baudrate,
                    timeout=0.001
                )
                core.wait(2.0)  # Allow Arduino auto-reset to settle
                logging.info(f"[PainCalibration] Arduino connected on {self.arduino_port}")
            except Exception as e:
                logging.warning(f"[PainCalibration] Serial port error: {e}. No hardware triggers")
                self.serial_dev = None
        else:
            logging.warning("[PainCalibration] PySerial not installed!!")

    def _instructions(self, exp_win, ctl_win):
        self.instruction_stim.draw(exp_win)
        if ctl_win:
            self.instruction_stim.draw(ctl_win)
        yield True  # Screen flip request

        for _ in utils.wait_until_yield(
            core.monotonicClock,
            core.getTime() + 4.0,
            keyboard_accuracy=0.01,
        ):
            yield

    def _send_trigger(self):
        """Fires pulse byte on selected Arduino board channel and resets to LOW."""
        onset_ts = self.task_timer.getTime()

        if self.serial_dev and self.serial_dev.is_open:
            self.serial_dev.write(bytes([self.trigger_byte]))  # Channel HIGH
            core.wait(self.pulse_duration)
            self.serial_dev.write(bytes([0]))                  # Channel LOW
        else:
            core.wait(self.pulse_duration)
            logging.info(f"[MOCK TRIGGER] Board Ch {self.target_channel} (byte {self.trigger_byte})")

        return onset_ts

    def _check_for_ttl(self):
        """Polls serial buffer for 'T' character sent from Arduino Pin 8."""
        if self.serial_dev and self.serial_dev.in_waiting > 0:
            incoming = self.serial_dev.read(self.serial_dev.in_waiting)
            if b'T' in incoming:
                return True
        return False

    def _run(self, exp_win, ctl_win):
        # Reset task clock upon entering _run
        self.task_timer.reset()

        # -------------------------------------------------------------
        # STEP 1: fMRI SYNCHRONIZATION WAIT LOOP
        # -------------------------------------------------------------
        if self.fmri:
            self.waiting_stim.draw(exp_win)
            if ctl_win:
                self.waiting_stim.draw(ctl_win)
            yield True  # Render "Waiting for scanner..."

            logging.info("[PainCalibration] Waiting for fMRI TTL pulse ('T' on Pin 8)...")
            
            # Flush existing buffer before waiting
            if self.serial_dev and self.serial_dev.is_open:
                self.serial_dev.reset_input_buffer()

            scanner_started = False
            while not scanner_started:
                # Check for Arduino 'T' trigger or keyboard key 't' (fallback)
                if self._check_for_ttl() or 't' in event.getKeys():
                    scanner_started = True
                    trigger_onset = self.task_timer.getTime()
                    logging.info(f"[fMRI SYNC] First scanner trigger received at t={trigger_onset:.4f}s!")
                    
                    # Reset clock so t=0 corresponds to fMRI run start
                    self.task_timer.reset()

                    self.events_log.append({
                        "onset": 0.0,
                        "duration": 0.0,
                        "trial_type": "fmri_trigger_start",
                        "channel": 8,
                        "byte_value": None,
                    })
                yield  # Yield control to main event loop without flipping screen unnecessarily

        # -------------------------------------------------------------
        # DISPLAY FIXATION CROSS
        # -------------------------------------------------------------
        for stim in self.fixation:
            stim.draw(exp_win)
        yield True

        # -------------------------------------------------------------
        # INITIAL BASELINE WAIT
        # -------------------------------------------------------------
        baseline_target = self.task_timer.getTime() + self.initial_wait
        for _ in utils.wait_until_yield(self.task_timer, baseline_target):
            # Log any subsequent volume TTL pulses if they arrive during scanning
            if self._check_for_ttl():
                self.events_log.append({
                    "onset": self.task_timer.getTime(),
                    "duration": 0.0,
                    "trial_type": "fmri_volume_trigger",
                    "channel": 8,
                    "byte_value": None,
                })
            yield

        # -------------------------------------------------------------
        # NOCICEPTIVE STIMULATION SEQUENCE
        # -------------------------------------------------------------
        for trial_idx in range(self.n_stim):
            trial_start_time = self.task_timer.getTime()

            # Fire physical trigger pulse to Digitimer
            onset_ts = self._send_trigger()

            # BIDS Event Logging
            self.events_log.append({
                "onset": onset_ts,
                "duration": self.pulse_duration,
                "trial_type": "nociceptive_stimulation",
                "channel": self.target_channel,
                "byte_value": self.trigger_byte,
            })

            # Inter-Stimulus Interval (ISI) Wait
            isi_target = trial_start_time + self.isi
            for _ in utils.wait_until_yield(self.task_timer, isi_target):
                if self._check_for_ttl():
                    self.events_log.append({
                        "onset": self.task_timer.getTime(),
                        "duration": 0.0,
                        "trial_type": "fmri_volume_trigger",
                        "channel": 8,
                        "byte_value": None,
                    })
                yield

        # -------------------------------------------------------------
        # FINAL BASELINE WAIT
        # -------------------------------------------------------------
        final_target = self.task_timer.getTime() + self.final_wait
        for _ in utils.wait_until_yield(self.task_timer, final_target):
            yield

    def _stop(self, exp_win, ctl_win):
        if self.serial_dev and self.serial_dev.is_open:
            self.serial_dev.write(bytes([0]))
            self.serial_dev.close()
            logging.info("[PainCalibration] Serial line closed cleanly.")
        yield True

    def _save(self):
        """Saves BIDS-compliant events.tsv log file."""
        if self.events_log:
            df = pd.DataFrame(self.events_log)
            cols = ["onset", "duration", "trial_type", "channel", "byte_value"]
            df = df[[c for c in cols if c in df.columns]]
            
            output_tsv = self._generate_unique_filename("events", "tsv")
            df.to_csv(output_tsv, sep="\t", index=False, float_format="%.4f")
            logging.info(f"[BIDS DATA SAVED] Event log saved to: {output_tsv}")
