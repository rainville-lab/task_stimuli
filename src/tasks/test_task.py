from psychopy import visual, event
from .task_base import Task

class TestTask(Task):
    """Minimal test task to verify PsychoPy window rendering and keypress input."""

    def __init__(self, name="test_task", **kwargs):
        super().__init__(name=name, **kwargs)

    def _instructions(self, exp_win, ctl_win):
        text = visual.TextStim(
            exp_win,
            text="PsychoPy Window Active!\n\nPress SPACE to complete task.\nPress Ctrl+Q to quit.",
            color=(1, 1, 1),
            height=0.08,
            wrapWidth=1.5,
        )
        
        event.clearEvents()
        waiting = True

        while waiting:
            text.draw()
            yield True  # Hand control to run_task_loop to flip window

            keys = event.getKeys()
            if "space" in keys:
                print("\n[TEST TASK] SPACE pressed! Moving forward...")
                waiting = False

    def _run(self, exp_win, ctl_win):
        text = visual.TextStim(
            exp_win,
            text="Task Running...\n\nPress 'ENTER' to finish.",
            color=(0, 1, 0),
            height=0.08,
        )

        event.clearEvents()
        waiting = True

        while waiting:
            text.draw()
            yield True

            keys = event.getKeys()
            if "return" in keys or "enter" in keys:
                print("\n[TEST TASK] ENTER pressed! Task finished.")
                waiting = False

    def _stop(self, exp_win, ctl_win):
        text = visual.TextStim(
            exp_win,
            text="Task complete! Closing...",
            color=(1, 1, 1),
            height=0.08,
        )
        for _ in range(60):  # Show for 1 second (~60 frames)
            text.draw()
            yield True
