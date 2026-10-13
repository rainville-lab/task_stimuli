"""Inter-stimulus-interval (ISI) functions for stimulation trains.

CONTRACT
--------
An ISI function is any callable

    isi_fn(stim_idx) -> float        # seconds

`stim_idx` is the position (0, 1, 2, ...) of the stimulation that has JUST been
delivered inside the current train (it restarts at 0 for every train). The
returned value is the time from the ONSET of that stimulation to the ONSET of
the next one (onset-to-onset, like "n TRs" in the original digitimer script).

If you want a sequence that spans the whole run (e.g. a pre-optimised list),
keep a counter inside your own function instead of relying on `stim_idx`.

Every factory below
  * returns such a function,
  * owns its random generator, so `seed=...` makes the sequence reproducible,
  * tags it with a `.description` string, which the task copies into the
    events .json sidecar (so each run documents which ISI scheme was used).

To use another scheme, change ONE line in the session file:

    stim_isi_fn=jittered_isi(8, 12, seed=1)   ->   stim_isi_fn=fixed_isi(10)
"""
import random


def _tag(fn, description):
    fn.description = description
    return fn


def fixed_isi(seconds=10.0):
    """Always the same ISI."""

    def isi_fn(stim_idx):
        return seconds

    return _tag(isi_fn, f"fixed_isi(seconds={seconds})")


def jittered_isi(low=8.0, high=12.0, seed=None):
    """Uniform random ISI in [low, high] seconds."""
    rng = random.Random(seed)

    def isi_fn(stim_idx):
        return rng.uniform(low, high)

    return _tag(isi_fn, f"jittered_isi(low={low}, high={high}, seed={seed})")


def list_isi(values):
    """Use a predefined list of ISIs (cycles if the train is longer)."""
    values = list(values)

    def isi_fn(stim_idx):
        return values[stim_idx % len(values)]

    return _tag(isi_fn, f"list_isi(values={values})")


def tr_multiple_isi(tr, choices=(4, 5, 6), seed=None):
    """ISI = tr x (a whole number of TRs picked at random from `choices`).

    Example: tr=3.0, choices=(4, 5, 6) -> ISI is 12, 15 or 18 s.
    choices=(4,) -> always 12 s, the same as `--tr 3.0 --isi 12` in the loopback
    script (trs_per_isi = round(isi / tr) = 4).

    Why: the ISI is jittered, but always a whole number of TRs, so consecutive
    stimulations of a train fall at the same position inside the TR. This only
    holds BETWEEN stimulations of a train: the first one is placed by the
    preceding steps (audio length, gaps), and nothing re-aligns the task clock
    to later TTLs, so a tiny clock drift between the computer and the scanner
    is possible over long runs.
    """
    rng = random.Random(seed)

    def isi_fn(stim_idx):
        return tr * rng.choice(choices)

    return _tag(isi_fn, f"tr_multiple_isi(tr={tr}, choices={tuple(choices)}, seed={seed})")


def truncated_exponential_isi(mean=10.0, low=6.0, high=20.0, seed=None):
    """Exponential ISI (mean `mean`), redrawn until it falls in [low, high]."""
    rng = random.Random(seed)

    def isi_fn(stim_idx):
        while True:
            value = rng.expovariate(1.0 / mean)
            if low <= value <= high:
                return value

    return _tag(
        isi_fn,
        f"truncated_exponential_isi(mean={mean}, low={low}, high={high}, seed={seed})",
    )
