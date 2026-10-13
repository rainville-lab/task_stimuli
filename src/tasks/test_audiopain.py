import os
import random
import time

from psychopy import core, data, event, sound, visual

from .task_base import Task
from ..shared import config, utils
from ..shared.eyetracking import fixation_dot


def jittered_isi(low=8.0, high=12.0):
    """Return an ISI function drawing uniformly from [low, high] seconds.

    Any callable with the signature isi_fn(stim_idx) -> seconds can be passed
    to PainAudio(stim_isi_fn=...) instead.
    """

    def isi_fn(stim_idx):
        return random.uniform(low, high)

    return isi_fn


class PainAudio(Task):
    """Per trial: audio -> ISI -> train of stimulations -> delay -> VAS -> ITI.

    The design is a csv/tsv file (or a list of dicts) with one row per trial
    and the columns:
        sound_file : path to the audio file
        n_stims    : number of stimulations in the train
        intensity  : stimulation intensity (device-specific units)
    """

    DEFAULT_INSTRUCTION = """
Please listen to the audio carefully.
You may then feel a series of stimulations.
After each series, rate how painful it was:
use left/right to move the cursor and press A to confirm.
"""
    INSTRUCTION_DURATION = 6

    def __init__(
        self,
        design,
        initial_wait=4,
        final_wait=9,
        audio_stim_isi=2.0,  # silence between end of audio and first stim
        stim_duration=1.0,
        stim_isi_fn=None,  # callable: stim_idx -> ISI in seconds
        stim_vas_delay=2.0,  # delay between last stim and VAS
        iti=3.0,
        vas_timeout=15.0,
        vas_step=5,
        vas_question="How painful was the stimulation?",
        vas_anchors=("No pain", "Worst pain imaginable"),
        max_intensity=None,  # safety: refuse to run if the design exceeds it
        stim_trigger=None,  # callable: (intensity, duration) -> None; None = dry run
        *args,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.initial_wait, self.final_wait = initial_wait, final_wait
        self.audio_stim_isi = audio_stim_isi
        self.stim_duration = stim_duration
        self.stim_isi_fn = stim_isi_fn or jittered_isi()
        self.stim_vas_delay = stim_vas_delay
        self.iti = iti
        self.vas_timeout, self.vas_step = vas_timeout, vas_step
        self.vas_question, self.vas_anchors = vas_question, vas_anchors
        self.stim_trigger = stim_trigger

        if isinstance(design, (str, os.PathLike)):
            if not os.path.exists(design):
                raise ValueError(f"{design} does not exist")
            self.design = data.importConditions(design)
        else:
            self.design = list(design)

        for row in self.design:
            if not os.path.exists(row["sound_file"]):
                raise ValueError(f"Audio file does not exist: {row['sound_file']}")
            if max_intensity is not None and row["intensity"] > max_intensity:
                raise ValueError(
                    f"Intensity {row['intensity']} exceeds max_intensity {max_intensity}"
                )

        self.duration = len(self.design)  # progress bar counts trials
        self._progress_bar_refresh_rate = None

    # ------------------------------------------------------------------ setup
    def _setup(self, exp_win):
        self.sounds = {
            f: sound.Sound(f) for f in {row["sound_file"] for row in self.design}
        }
        self.fixation = fixation_dot(exp_win)
        self._build_vas(exp_win)
        self._restart()

    def _restart(self):
        self.trials = data.TrialHandler(self.design, 1, method="sequential")

    def _build_vas(self, exp_win):
        self._vas_extent = exp_win.size[0] * 0.25
        ext = self._vas_extent
        self._vas_prompt = visual.TextStim(
            exp_win,
            text=self.vas_question,
            units="pix",
            pos=(0, 150),
            height=36,
            wrapWidth=exp_win.size[0] * 0.8,
            color="white",
        )
        self._vas_line = visual.Line(
            exp_win,
            (-ext, 0),
            (ext, 0),
            units="pix",
            lineWidth=6,
            lineColor="white",
            autoLog=False,
        )
        self._vas_marker = visual.Circle(
            exp_win,
            units="pix",
            radius=12,
            pos=(0, 0),
            fillColor="red",
            lineColor="red",
            autoLog=False,
        )
        self._vas_labels = [
            visual.TextStim(
                exp_win,
                text=txt,
                units="pix",
                pos=(x, -50),
                height=28,
                wrapWidth=ext,
                color="white",
            )
            for txt, x in zip(self.vas_anchors, (-ext, ext))
        ]

    # ---------------------------------------------------------- instructions
    def _instructions(self, exp_win, ctl_win):
        screen_text = visual.TextStim(
            exp_win,
            text=self.instruction,
            alignText="center",
            color="white",
            wrapWidth=config.WRAP_WIDTH,
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

    # --------------------------------------------------------------- helpers
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

    def _deliver_stimulation(self, intensity, duration):
        """Hardware hook. Replace/inject via stim_trigger."""
        if self.stim_trigger is None:
            print(f"[DRY RUN] stimulation: intensity={intensity}, duration={duration}s")
        else:
            self.stim_trigger(intensity, duration)

    def _vas(self, exp_win, trial_n):
        keys_of_interest = ["l", "r", "a"]
        ext = self._vas_extent
        value = 50
        responded = False
        t_on, t_resp = None, None
        n_flips = 0
        event.getKeys(keys_of_interest)  # flush

        while True:
            moved = False
            for key, t_key in event.getKeys(keys_of_interest, timeStamped=self.task_timer):
                if key == "l":
                    value, moved = max(0, value - self.vas_step), True
                elif key == "r":
                    value, moved = min(100, value + self.vas_step), True
                elif key == "a":
                    responded, t_resp = True, t_key
            if responded:
                break
            if t_on is not None and self.task_timer.getTime() - t_on > self.vas_timeout:
                break
            if not moved and n_flips > 1:
                time.sleep(0.005)
                yield  # lets the main loop handle shortcuts (ctrl-c, ctrl-q...)
                continue

            self._vas_marker.pos = (-ext + 2 * ext * value / 100, 0)
            for stim in [self._vas_prompt, self._vas_line, self._vas_marker] + self._vas_labels:
                stim.draw(exp_win)
            yield True
            if t_on is None:
                t_on = self._exp_win_last_flip_time - self._exp_win_first_flip_time
            n_flips += 1

        self._log_event(
            {
                "trial_type": "vas-rating",
                "trial": trial_n,
                "value": value if responded else "n/a",
                "response_time": (t_resp - t_on) if responded else "n/a",
                "task_time": self.task_timer.getTime(),
            }
        )

    # ------------------------------------------------------------------- run
    def _run(self, exp_win, ctl_win):
        yield from self._fixation(exp_win, self.initial_wait)

        for trial_n, trial in enumerate(self.trials):
            self.progress_bar.set_description(f"Trial {trial_n}:")
            self.progress_bar.update(1)

            # 1. audio
            snd = self.sounds[trial["sound_file"]]
            snd.play()
            t_audio = self.task_timer.getTime()
            self._log_event(
                {
                    "trial_type": "audio",
                    "trial": trial_n,
                    "sound_file": trial["sound_file"],
                    "audio_duration": snd.duration,
                    "task_time": t_audio,
                }
            )
            yield from self._fixation(exp_win, t_audio + snd.duration)
            while snd.status > 0:
                yield

            # 2. silence between audio and first stimulation
            yield from self._fixation(exp_win, self.task_timer.getTime() + self.audio_stim_isi)

            # 3. stimulation train with custom ISI
            n_stims = int(trial["n_stims"])
            for stim_idx in range(n_stims):
                isi = float(self.stim_isi_fn(stim_idx)) if stim_idx < n_stims - 1 else 0.0
                t_stim = self.task_timer.getTime()
                self._deliver_stimulation(trial["intensity"], self.stim_duration)
                self._log_event(
                    {
                        "trial_type": "stimulation",
                        "trial": trial_n,
                        "stim_idx": stim_idx,
                        "intensity": trial["intensity"],
                        "stim_duration": self.stim_duration,
                        "isi_after": isi,
                        "task_time": t_stim,
                    }
                )
                yield from self._fixation(exp_win, t_stim + self.stim_duration + isi)

            # 4. delay, then VAS rating
            yield from self._fixation(exp_win, self.task_timer.getTime() + self.stim_vas_delay)
            self.progress_bar.set_description(f"Trial {trial_n}: rating")
            yield from self._vas(exp_win, trial_n)

            # 5. inter-trial interval
            yield from self._fixation(exp_win, self.task_timer.getTime() + self.iti)

        print(f"{'*'*25} PREPARE TO STOP {'*'*25}")
        yield from self._fixation(exp_win, self.task_timer.getTime() + self.final_wait)
        print(f"{'#'*25} STOP SCANNER    {'#'*25}")

    def _stop(self, exp_win, ctl_win):
        for snd in getattr(self, "sounds", {}).values():
            snd.stop()
        yield