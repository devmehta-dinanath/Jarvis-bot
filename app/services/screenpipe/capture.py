import shlex
import shutil
import subprocess
from pathlib import Path

from app import models
from app.config import DEFAULT_CAPTURE_FILENAME
from app.services.media.storage import RecordingPaths


def resolve_video_source(
    recording: models.Recording,
    paths: RecordingPaths,
    capture_command: str | None = None,
) -> Path:
    output_video_path = paths.screenpipe / DEFAULT_CAPTURE_FILENAME
    command_template = capture_command or recording.capture_command
    if command_template:
        command = command_template.format(output=str(output_video_path))
        subprocess.run(
            shlex.split(command),
            check=True,
            capture_output=True,
            text=True,
        )
        recording.source_video_path = str(output_video_path)
        return output_video_path
    
    if not recording.source_video_path:
        raise ValueError("Provide either source_video_path or capture_command to start a recording job.")

    source_path = Path(recording.source_video_path).expanduser().resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"Source video not found: {source_path}")

    if source_path.parent != paths.screenpipe:
        destination = paths.screenpipe / source_path.name
        shutil.copy2(source_path, destination)
        recording.source_video_path = str(destination)
        return destination

    return source_path


def capture_single_frame(
    paths: RecordingPaths,
    frame_index: int,
    capture_command: str,
) -> Path:
    frame_path = paths.frames / f"frame_{frame_index:06d}.jpg"
    command = capture_command.format(output=str(frame_path))
    subprocess.run(
        shlex.split(command),
        check=True,
        capture_output=True,
        text=True,
    )
    if not frame_path.exists():
        raise RuntimeError(f"Capture command did not produce a frame: {frame_path}")
    return frame_path
