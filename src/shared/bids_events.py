"""Incremental BIDS events writer: <name>_events.tsv + <name>_events.json sidecar.

Usage
-----
    logger = BIDSEventLogger({"trial": {"Description": "Trial index"}, ...})
    logger.open(tsv_path, task_name="painaudio", repetition_time=3.0, parameters={...})
    logger.set_t0(t)                    # time of the first TTL (or run start)
    logger.log("stimulation", t_stim, 0.01, trial=0)   # appended + flushed to disk NOW
    logger.close()

* The TSV header and the .json sidecar are written by `open()`. Every `log()`
  appends one row and flushes it, so a crash loses at most the event being written.
* `onset`, `duration`, `trial_type` are always the first three columns.
* Extra columns are the keys of the dict given to the constructor; passing an
  unknown column to `log()` raises, so typos are caught in the first dry run.
* Times given to `log()` are on whatever clock you use (the task uses
  `task_timer`); onsets are written relative to `t0`.
* Missing values are written as `n/a`, as the BIDS specification asks.
"""
import csv
import json
import os

CORE_COLUMNS = {
    "onset": {
        "Description": "Event onset relative to the first scanner TTL (t0), "
        "or to the start of the run when no TTL synchronisation is used",
        "Units": "seconds",
    },
    "duration": {"Description": "Duration of the event or trigger pulse", "Units": "seconds"},
    "trial_type": {"Description": "Category of the event"},
}


class BIDSEventLogger:
    def __init__(self, extra_columns=None):
        self.extra_columns = dict(extra_columns or {})
        self.columns = {**CORE_COLUMNS, **self.extra_columns}
        self.t0 = None
        self.tsv_path = None
        self._file = None
        self._writer = None

    def open(self, tsv_path, task_name, repetition_time=None, parameters=None):
        """Create the TSV (with header) and the .json sidecar."""
        self.close()
        self.t0 = None
        self.tsv_path = tsv_path
        self._file = open(tsv_path, "w", newline="")
        self._writer = csv.writer(self._file, delimiter="\t")
        self._writer.writerow(list(self.columns))
        self._file.flush()

        sidecar = {"TaskName": task_name}
        if repetition_time is not None:
            sidecar["RepetitionTime"] = repetition_time
        sidecar.update(self.columns)
        if parameters:
            sidecar["ExperimentParameters"] = parameters
        with open(os.path.splitext(tsv_path)[0] + ".json", "w") as f:
            json.dump(sidecar, f, indent=4, default=str)
        print(f"[BIDS] writing events to {tsv_path}")

    def set_t0(self, t0):
        self.t0 = t0

    @staticmethod
    def _fmt(value):
        return f"{value:.4f}" if isinstance(value, float) else value

    def log(self, event_type, time, duration=0.0, **fields):
        """Append one event (`event_type` goes in the trial_type column)."""
        if self._file is None:
            raise RuntimeError("BIDSEventLogger.open() must be called before log()")
        if self.t0 is None:
            raise RuntimeError("BIDSEventLogger.set_t0() must be called before log()")
        unknown = set(fields) - set(self.extra_columns)
        if unknown:
            raise KeyError(f"Unknown event column(s): {sorted(unknown)}")
        row = {name: "n/a" for name in self.columns}
        row.update(fields)
        row["onset"] = time - self.t0
        row["duration"] = duration
        row["trial_type"] = event_type
        self._writer.writerow([self._fmt(row[name]) for name in self.columns])
        self._file.flush()

    def close(self):
        if self._file is not None:
            self._file.close()
            self._file = self._writer = None
            print(f"[BIDS] events saved to {self.tsv_path}")
