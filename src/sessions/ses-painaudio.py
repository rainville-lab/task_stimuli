"""Session `painaudio` (audio -> ISI -> pain -> rating).

Run it from the repo root, with the same flags you used for the audio test:
    python main.py --subject sub-test --session 01 --tasks painaudio --output ./derivatives/

Everything you normally change is in the block below. The PainAudio(...) call
at the end is built when main.py loads this file, so a wrong path or a typo in
the design / steps fails immediately, before any window opens.
"""
from ..shared import fmri
from ..shared.isi import jittered_isi
from ..shared.stimulator import DryRunStimulator
from ..tasks.painaudio import PainAudio


# ============================== EDIT HERE ==============================

# 1. Hardware
STIMULATOR = DryRunStimulator()

# 2. Run synchronization
START_SYNC = "none"

# 3. Stimulation timing
TR = fmri.MR_settings["TR"]
STIM_ISI_FN = jittered_isi(8, 12, seed=1)
STIM_COPY_CHANNELS = (1,)

# 4. Trial structure

PAIN_RATING = "À quel point la douleur était-elle intense?"

STEPS = [
    ("audio", "hypnosis_audio"),
    ("gap", 2.0),
    ("stim", 10), # + ISIs from the STIM_ISI_FN function
    ("audio", "attention_audio"),
    ("gap", 2.0),
    ("rating", PAIN_RATING),
]

# 5. Trial sequence
DESIGN = "data/test_audio/design.csv"

# =======================================================================

TASKS = [
    PainAudio(
        design=DESIGN,
        steps=STEPS,
        stimulator=STIMULATOR,
        stim_isi_fn=STIM_ISI_FN,
        stim_copy_channels=STIM_COPY_CHANNELS,
        start_sync=START_SYNC,
        tr=TR,
        name="task-painaudio",
    ),
]