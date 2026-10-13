from src.tasks.paincalibration import PainCalibration


def get_tasks(parsed):
    return [
        PainCalibration(
            n_stim=parsed.n_stim if hasattr(parsed, "n_stim") else 5,
            isi=parsed.isi if hasattr(parsed, "isi") else 5.0,
            target_channel=0,        # Board Channel 0 (Byte 1) -> Digitimer SPP
            pulse_duration=0.050,    # 50 ms pulse width
            arduino_port="/dev/ttyACM0",
            fmri=parsed.fmri,        # Reads --fmri flag from CLI
            name="paincalibration",
        )
    ]
