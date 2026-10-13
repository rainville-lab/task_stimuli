import os

from src.tasks.test_audiopain import PainAudio

from ..tasks.test_audio import TestAudio


STIMULI_PATH = "data/test_audio"


TASKS = [
    PainAudio(
        sound_file=os.path.join(
            STIMULI_PATH,
            "botte_caoutchouc.wav",
        ),
        name="task-PainAudio_run-01",
    ),
]