"""PainAudio: one RUN = one instance of this task = one sync = one events file.

A run is a list of TRIALS (the rows of the design file, played in file order).
Each trial is a sequence of STEPS, so a trial is described by data, not code:

    steps=[("audio", "audio_1"), ("gap", 2.0), ("stim", "n_stims"),
           ("audio", "audio_2"), ("rating", "How painful was it?"), ("audio", "audio_3")]

Steps (each one is a `_step_<name>` method):
    ("audio",  column_or_path)     play a sound file and wait until it ends
    ("gap",    seconds)            fixation only (e.g. an inter-stimulus interval)
    ("stim",   column_or_int)      train of stimulations, ISI from `stim_isi_fn`
    ("rating", question_or_dict)   rating scale; a dict is
                                   {"question": ..., "anchors": (left, right)}

The 2nd element of a step is looked up in the trial's design row first
("audio_1" = "the file named in column audio_1"); if it is not a column name it
is used as a literal (a path, a number, a question).

Different trial structures per condition: pass a dict instead of a list, and
give the design a `condition` column:

    steps={"A": [("audio", "audio_1"), ("gap", 2.0), ("stim", "n_stims"), ("rating", "...")],
           "B": [("audio", "audio_1"), ("rating", "...")]}

Design file (csv/tsv, one row per trial), e.g.:
    condition,audio_1,n_stims
    A,data/painaudio/xxx.wav,3
    B,data/painaudio/yyy.wav,

START OF THE RUN (`start_sync`, defines t0 = onset 0 of the events file):
    "arduino"  the task waits for TTL(s) coming through the Arduino (stimulator)
    "none"     t0 = start of the run. Use it outside the scanner, or with --fmri:
               cli.run_task has then ALREADY waited for the keyboard TTL
               (fmri.wait_for_ttl) just before calling _run.

Methods the framework calls (see tasks/task_base.py and shared/cli.py):
    setup -> _setup, instructions -> _instructions, run -> _run, stop -> _stop,
    save -> _save (always, in a `finally`), restart -> _restart, unload.
Inside generators, `yield True` = flip the window(s); a plain `yield` = hand
control back to the main loop without flipping (shortcuts keep working).
"""
import os
import platform
import subprocess
import time

import psychopy
from psychopy import core, data, event, sound, visual

from .task_base import Task
from ..shared import config, fmri, utils
from ..shared.bids_events import BIDSEventLogger
from ..shared.eyetracking import fixation_dot
from ..shared.isi import jittered_isi
from ..shared.stimulator import DryRunStimulator


def _git_info():
    """Commit and 'dirty' flag of the code that ran (goes in the events sidecar)."""
    here = os.path.dirname(os.path.abspath(__file__))

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=here, capture_output=True, text=True, timeout=2
        ).stdout.strip()

    try:
        return {
            "commit": git("rev-parse", "--short", "HEAD") or "unknown",
            "uncommitted_changes": bool(git("status", "--porcelain", "--untracked-files=no")),
        }
    except Exception:
        return {"commit": "unknown", "uncommitted_changes": "unknown"}


class PainAudio(Task):
    DEFAULT_INSTRUCTION = """
Please listen to the audio carefully.
You may then feel a series of stimulations.
After each series, rate how painful it was:
use left/right to move the cursor and press A to confirm.
"""
    INSTRUCTION_DURATION = 6

    # Extra columns of the BIDS events.tsv (onset, duration and trial_type are
    # always there; trial_type = kind of event: scan_ttl, audio, stimulation, rating).
    # To log something new: add the column here, then pass it to self._log(...).
    EVENT_COLUMNS = {
        "trial": {"Description": "Trial index in the run (0-based)"},
        "condition": {"Description": "Condition of the trial (design column 'condition')"},
        "stim_count": {"Description": "Index of the stimulation within the run (1-based)"},
        "channel": {"Description": "Arduino channels pulsed (ch0 = stimulator trigger)"},
        "isi_after": {
            "Description": "Time from this stimulation onset to the next one in the train",
            "Units": "seconds",
        },
        "stim_file": {"Description": "Audio file played"},
        "value": {"Description": "Rating, 0 (left anchor) to 100 (right anchor)"},
        "response_time": {"Description": "Rating response time from rating onset", "Units": "seconds"},
        "notes": {"Description": "Free text"},
        "sample": {"Description": "time.monotonic() when the event was logged (as in Task._log_event)"},
    }

    def __init__(
        self,
        design,
        steps,  # list of steps, or dict {condition: list of steps}
        stimulator=None,  # a Stimulator; None -> DryRunStimulator (no hardware)
        stim_isi_fn=None,  # callable stim_idx -> seconds (see shared/isi.py)
        stim_copy_channels=(1,),  # copies of each stim trigger (Arduino channels)
        start_sync="arduino",  # "arduino" | "none" (see module docstring)
        start_after_n_trs=1,  # arduino sync: start at the Nth TTL (t0 = 1st TTL)
        initial_wait=4,
        final_wait=9,
        tr=None,  # seconds; default = fmri.MR_settings["TR"]
        rating_timeout=15.0,
        rating_increment=5,
        rating_anchors=("No pain", "Worst pain imaginable"),
        *args,
        **kwargs,
    ):
        super().__init__(**kwargs)
        if start_sync not in ("arduino", "none"):
            raise ValueError(f"start_sync must be 'arduino' or 'none', got {start_sync!r}")
        self.stimulator = stimulator or DryRunStimulator()
        self.stim_isi_fn = stim_isi_fn or jittered_isi()
        self.stim_copy_channels = tuple(stim_copy_channels)
        self.start_sync = start_sync
        self.start_after_n_trs = start_after_n_trs
        self.initial_wait, self.final_wait = initial_wait, final_wait
        self.tr = tr if tr is not None else fmri.MR_settings["TR"]
        self.rating_timeout = rating_timeout
        self.rating_increment = rating_increment
        self.rating_anchors = rating_anchors

        # --- design: a csv/tsv path or a list of dicts
        if isinstance(design, (str, os.PathLike)):
            if not os.path.exists(design):
                raise ValueError(f"{design} does not exist")
            self.design = data.importConditions(design)
            self.design_source = str(design)
        else:
            self.design = list(design)
            self.design_source = "inline list"

        # --- trial structure(s): one list for all trials, or one per condition
        if isinstance(steps, dict):
            self.structures = {k: [tuple(s) for s in v] for k, v in steps.items()}
            self._default_structure = None
        else:
            self.structures = {}
            self._default_structure = [tuple(s) for s in steps]

        # --- validate everything now, so a typo or a missing file fails before
        #     the session starts instead of in the middle of a run
        for n, row in enumerate(self.design):
            for kind, arg in self._steps_for(row):
                if not hasattr(self, f"_step_{kind}"):
                    raise ValueError(f"Unknown step '{kind}' (there is no _step_{kind} method)")
                if kind == "audio":
                    path = self._resolve(row, arg)
                    if not os.path.exists(path):
                        raise ValueError(f"Design row {n}: audio file does not exist: {path}")
                elif kind == "stim":
                    try:
                        int(self._resolve(row, arg))
                    except (TypeError, ValueError):
                        raise ValueError(
                            f"Design row {n}: step ('stim', {arg!r}) needs an integer or a "
                            f"design column holding one, got {self._resolve(row, arg)!r}"
                        ) from None
                elif kind == "gap":
                    float(arg)

        self.duration = len(self.design)  # progress bar counts trials
        self._progress_bar_refresh_rate = None  # we update it ourselves, once per trial

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _resolve(trial, arg):
        """Return trial[arg] if `arg` is a design column, otherwise `arg` itself."""
        return trial[arg] if isinstance(arg, str) and arg in trial else arg

    def _steps_for(self, trial):
        """The list of steps for this trial (depends on its condition if a dict was given)."""
        if self._default_structure is not None:
            return self._default_structure
        condition = trial["condition"] if "condition" in trial else None
        if condition not in self.structures:
            raise ValueError(
                f"Trial condition {condition!r} has no structure; known: {list(self.structures)}"
            )
        return self.structures[condition]

    def _log(self, event_type, t, duration=0.0, **fields):
        """Log one event. Adds trial, condition and a monotonic `sample` automatically."""
        if self._trial_n is not None:
            fields.setdefault("trial", self._trial_n)
            fields.setdefault("condition", self._condition)
        fields.setdefault("sample", time.monotonic())
        self.logger.log(event_type, t, duration, **fields)

    # ------------------------------------------------------------------ setup
    def _setup(self, exp_win):
        """Build everything that needs the window; connect to the hardware."""
        audio_files = {
            self._resolve(row, arg)
            for row in self.design
            for kind, arg in self._steps_for(row)
            if kind == "audio"
        }
        self.sounds = {f: sound.Sound(f) for f in audio_files}
        self.fixation = fixation_dot(exp_win)
        self._build_rating(exp_win)
        self.stimulator.open()
        self._restart()

    def _restart(self):
        """Reset the run state (also called by the framework when the task restarts)."""
        if getattr(self, "logger", None) is not None:
            self.logger.close()  # keeps the file of the aborted attempt
        self.trials = data.TrialHandler(self.design, 1, method="sequential")
        self.logger = BIDSEventLogger(self.EVENT_COLUMNS)
        self._stim_counter = 0
        self._trial_n = None
        self._condition = None

    def _build_rating(self, exp_win):
        ext = self._rating_extent = exp_win.size[0] * 0.25
        self._rating_prompt = visual.TextStim(
            exp_win, text="", units="pix", pos=(0, 150), height=36,
            wrapWidth=exp_win.size[0] * 0.8, color="white",
        )
        self._rating_line = visual.Line(
            exp_win, (-ext, 0), (ext, 0), units="pix",
            lineWidth=6, lineColor="white", autoLog=False,
        )
        self._rating_marker = visual.Circle(
            exp_win, units="pix", radius=12, pos=(0, 0),
            fillColor="red", lineColor="red", autoLog=False,
        )
        self._rating_labels = [  # texts are set at each rating step
            visual.TextStim(
                exp_win, text="", units="pix", pos=(x, -50),
                height=28, wrapWidth=ext, color="white",
            )
            for x in (-ext, ext)
        ]

    # ---------------------------------------------------------- instructions
    def _instructions(self, exp_win, ctl_win):
        screen_text = visual.TextStim(
            exp_win, text=self.instruction, alignText="center",
            color="white", wrapWidth=config.WRAP_WIDTH,
        )
        for flip_idx, _ in enumerate(
            utils.wait_until_yield(
                core.monotonicClock,
                core.getTime() + self.INSTRUCTION_DURATION,
                keyboard_accuracy=0.01,
            )
        ):
            if flip_idx < 2:
                screen_text.draw(exp_win)
                if ctl_win:
                    screen_text.draw(ctl_win)
                yield True
            yield

    # ----------------------------------------------------------- run helpers
    def _fixation(self, exp_win, offset):
        """Show the fixation dot until task_timer reaches `offset` (seconds)."""
        for flip, _ in enumerate(
            utils.wait_until_yield(
                self.task_timer,
                offset - 1 / (config.FRAME_RATE + 1),
                keyboard_accuracy=0.1,
            )
        ):
            if flip < 2:
                for stim in self.fixation:
                    stim.draw(exp_win)
                yield True

    def _wait_for_ttl(self, exp_win, ctl_win):
        """Wait for `start_after_n_trs` TTLs read through the stimulator (Arduino).

        The FIRST TTL defines t0 (onset 0 in the events file). Every TTL seen
        here is logged as a `scan_ttl` event. TTL timestamps are as precise as
        the main-loop polling (about a millisecond if the loop is not throttled).
        """
        status = None
        if ctl_win:
            status = visual.TextStim(
                exp_win, text="Waiting for scanner TTL...", color="white",
                wrapWidth=config.WRAP_WIDTH,
            )
        self.stimulator.poll_ttl()  # discard TTLs received before we started waiting
        for stim in self.fixation:
            stim.draw(exp_win)
        if status:
            status.draw(ctl_win)
        yield True

        n_ttl = 0
        while n_ttl < self.start_after_n_trs:
            utils.poll_windows()  # keeps keyboard shortcuts (ctrl-c/n/q) alive
            n_new = self.stimulator.poll_ttl()
            if n_new:
                t_ttl = self.task_timer.getTime()
                for _ in range(n_new):
                    n_ttl += 1
                    if n_ttl == 1:
                        self.logger.set_t0(t_ttl)
                    self._log("scan_ttl", t_ttl, 0.001, notes=f"TTL #{n_ttl}")
                    print(f"TTL #{n_ttl} received")
            time.sleep(0.0005)
            yield

    # ------------------------------------------------------------------ steps
    # Every step has the same signature: (exp_win, trial, arg).
    # To add a new kind of step, write a `_step_<name>` method and use
    # ("<name>", arg) in `steps`.
    def _step_audio(self, exp_win, trial, arg):
        path = self._resolve(trial, arg)
        snd = self.sounds[path]
        t_audio = self.task_timer.getTime()
        snd.play()
        self._log("audio", t_audio, snd.duration, stim_file=path)
        yield from self._fixation(exp_win, t_audio + snd.duration)
        while snd.status > 0:  # in case the backend reports a longer duration
            yield

    def _step_gap(self, exp_win, trial, arg):
        yield from self._fixation(exp_win, self.task_timer.getTime() + float(arg))

    def _step_stim(self, exp_win, trial, arg):
        n_stims = int(self._resolve(trial, arg))
        channels = "+".join(
            f"ch{c}" for c in (self.stimulator.STIM_CHANNEL, *self.stim_copy_channels)
        )
        for stim_idx in range(n_stims):
            # ISI is drawn BEFORE firing so the pulse timestamp is not delayed.
            is_last = stim_idx == n_stims - 1
            isi = "n/a" if is_last else float(self.stim_isi_fn(stim_idx))

            t_stim = self.task_timer.getTime()
            self.stimulator.trigger(copy_channels=self.stim_copy_channels)
            self._stim_counter += 1
            self._log(
                "stimulation", t_stim, self.stimulator.pulse_dur,
                stim_count=self._stim_counter, channel=channels, isi_after=isi,
            )
            if not is_last:
                # ISI is onset-to-onset, so wait until t_stim + isi.
                yield from self._fixation(exp_win, t_stim + isi)

    def _step_rating(self, exp_win, trial, arg):
        spec = self._resolve(trial, arg)
        if isinstance(spec, dict):
            question = spec["question"]
            anchors = spec.get("anchors", self.rating_anchors)
        else:
            question, anchors = spec, self.rating_anchors
        self._rating_prompt.text = question
        for label, text in zip(self._rating_labels, anchors):
            label.text = text

        ext = self._rating_extent
        value, responded = 50, False
        t_on = t_resp = None
        n_flips = 0

        # Temp keyboard key. To Do : connect external device keys
        # ------------------------------
        keys_of_interest = ["i", "o", "p"]
        event.getKeys(keys_of_interest)  # flush old presses

        while True:
            utils.poll_windows()  # keeps keyboard shortcuts (ctrl-c/n/q) alive
            moved = False
            for key, t_key in event.getKeys(keys_of_interest, timeStamped=self.task_timer):
                if key == "l":
                    value, moved = max(0, value - self.rating_increment), True
                elif key == "r":
                    value, moved = min(100, value + self.rating_increment), True
                elif key == "a" and t_on is not None:
                    responded, t_resp = True, t_key
            if responded:
                break
            if t_on is not None and self.task_timer.getTime() - t_on > self.rating_timeout:
                break
            if not moved and n_flips > 1:
                time.sleep(0.005)
                yield  # lets the main loop handle shortcuts (ctrl-c, ctrl-q...)
                continue

            self._rating_marker.pos = (-ext + 2 * ext * value / 100, 0)
            for stim in [self._rating_prompt, self._rating_line, self._rating_marker] + self._rating_labels:
                stim.draw(exp_win)
            yield True
            if t_on is None:
                t_on = self.task_timer.getTime()  # ~ flip time (one frame accuracy)
            n_flips += 1

        t_end = t_resp if responded else self.task_timer.getTime()
        self._log(
            "rating", t_on, t_end - t_on,
            value=value if responded else "n/a",
            response_time=(t_resp - t_on) if responded else "n/a",
            notes=question,
        )

    # ------------------------------------------------------------------- run
    def _run(self, exp_win, ctl_win):
        # 0. create the events file now; rows are appended as events happen
        self.logger.open(
            self._generate_unique_filename("events", "tsv"),
            task_name=self.name,
            repetition_time=self.tr,
            parameters=self._sidecar_parameters(),
        )

        # 1. synchronise the start of the run (defines t0 for the events file)
        if self.start_sync == "arduino":
            if self.use_fmri:
                print(
                    "[WARNING] --fmri is on: the framework already waited for a keyboard TTL, "
                    "and the task will now ALSO wait for the Arduino TTL. "
                    "Use start_sync='none' to rely on the framework's TTL only."
                )
            yield from self._wait_for_ttl(exp_win, ctl_win)
        else:
            self.logger.set_t0(self.task_timer.getTime())
        yield from self._fixation(exp_win, self.task_timer.getTime() + self.initial_wait)

        # 2. trials = one sequence of steps per design row
        for trial_n, trial in enumerate(self.trials):

            self._trial_n = trial_n
            self._condition = trial["condition"] if "condition" in trial else "n/a"
            self.progress_bar.set_description(f"Trial {trial_n} ({self._condition}):")
            for kind, arg in self._steps_for(trial):
                yield from getattr(self, f"_step_{kind}")(exp_win, trial, arg)
            self.progress_bar.update(1)
        self._trial_n = self._condition = None

        print(f"{'*'*25} PREPARE TO STOP {'*'*25}")
        yield from self._fixation(exp_win, self.task_timer.getTime() + self.final_wait)
        print(f"{'#'*25} STOP SCANNER    {'#'*25}")

    def _stop(self, exp_win, ctl_win):
        for snd in getattr(self, "sounds", {}).values():
            snd.stop()
        self.stimulator.all_off()  # make sure every Arduino channel is low
        yield

    def unload(self):
        if getattr(self, "logger", None) is not None:
            self.logger.close()
        self.stimulator.close()  # channels low; the Arduino port stays open

    # ------------------------------------------------------------------ save
    def _sidecar_parameters(self):
        """Everything needed to know HOW this run was produced (goes in the .json)."""
        if self._default_structure is not None:
            steps = [list(s) for s in self._default_structure]
        else:
            steps = {k: [list(s) for s in v] for k, v in self.structures.items()}
        return {
            "design": self.design_source,
            "steps": steps,
            "stim_isi_fn": getattr(self.stim_isi_fn, "description", repr(self.stim_isi_fn)),
            "stim_copy_channels": list(self.stim_copy_channels),
            "pulse_duration": self.stimulator.pulse_dur,
            "stimulator": type(self.stimulator).__name__,
            "start_sync": self.start_sync,
            "start_after_n_trs": self.start_after_n_trs,
            "used_fmri_flag": bool(self.use_fmri),
            "initial_wait": self.initial_wait,
            "final_wait": self.final_wait,
            "rating_timeout": self.rating_timeout,
            "code_version": _git_info(),
            "psychopy_version": getattr(psychopy, "__version__", "unknown"),
            "python_version": platform.python_version(),
        }

    def _save(self):
        # Events were written as the run went; this only closes the file.
        # Returning False tells Task.save() not to write its own events file.
        self.logger.close()
        return False
