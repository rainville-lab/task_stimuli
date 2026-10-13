import os

from psychopy import prefs
prefs.hardware['audioLib'] = ['ptb', 'sounddevice'] # fix for warning : 
# We strongly recommend you activate the PTB sound engine in PsychoPy prefs as the preferred audio engine. Its timing is vastly superior.
from psychopy import sound

from .task_base import Task
from ..shared import utils


class TestAudio(Task):
    """Minimal task for testing audio playback through task_stimuli."""

    def __init__(
        self,
        sound_file,
        initial_wait=2,
        final_wait=2,
        *args,
        **kwargs,
    ):
        super().__init__(**kwargs)

        if not os.path.exists(sound_file):
            raise ValueError(f"Audio file does not exist: {sound_file}")

        self.sound_file = sound_file
        self.initial_wait = initial_wait
        self.final_wait = final_wait

    def _setup(self, exp_win):
        self.sound = sound.Sound(self.sound_file)
        self.duration = self.sound.duration

    def _run(self, exp_win, ctl_win):
        # Wait before playing audio.
        yield from utils.wait_until_yield(
            self.task_timer,
            self.initial_wait,
            keyboard_accuracy=0.1,
        )

        # Play audio.
        print(f"Playing audio: {self.sound_file}")
        print(f"Duration: {self.sound.duration:.2f} s")

        self.sound.play()

        # Keep the task alive while the audio plays.
        yield from utils.wait_until_yield(
            self.task_timer,
            self.initial_wait + self.sound.duration,
            keyboard_accuracy=0.01,
        )

        # Small final wait after audio.
        yield from utils.wait_until_yield(
            self.task_timer,
            self.initial_wait + self.sound.duration + self.final_wait,
            keyboard_accuracy=0.01,
        )

        print("Audio finished.")

    def _stop(self, exp_win, ctl_win):
        self.sound.stop()
        yield