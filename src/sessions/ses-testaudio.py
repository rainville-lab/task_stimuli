import os

from ..tasks.test_audio import TestAudio


STIMULI_PATH = "data/test_audio"


TASKS = [
    TestAudio(
        sound_file=os.path.join(
            STIMULI_PATH,
            "botte_caoutchouc.wav",
        ),
        name="task-TestAudio_run-01",
    ),
]