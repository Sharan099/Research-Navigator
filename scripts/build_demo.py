"""Build a narrated 60-second edit from verified offline footage, without application/provider access.

Requires Node/Playwright, full FFmpeg, Piper and its downloaded LJ Speech voice.
FFMPEG may name a full executable.
Media outputs stay in ignored showcase/demo-60s; source footage is opened read-only.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / ".runtime/demo-build"
OUTPUT = ROOT / "showcase/demo-60s"
SOURCE = ROOT / "showcase/research-navigator-demo.webm"

NARRATION = [
    (0, 5, "Research Navigator. Discover scientific publications and explore the available evidence."),
    (
        5,
        15,
        "Start with a research question in plain English. Refine its scope using clarification, date filters, "
        "and sorting. This recording explores saved tactile sensing research, with external provider access disabled.",
    ),
    (
        20,
        15,
        "The backend is designed to collect OpenAlex metadata, preserve raw records, validate fields, normalize "
        "identifiers, and update SQL storage by provider identity. Ingestion counts are reconciled. "
        "This diagram explains that architecture.",
    ),
    (
        35,
        15,
        "Inspect publication details and continue in the same conversation. Here, a genuine saved Gemini "
        "response demonstrates supporting quotes from available abstracts. This is a historical replay, "
        "not a new model response.",
    ),
    (
        50,
        10,
        "Explore the code, architecture, and reproducible evaluations on GitHub. Research Navigator. "
        "A practical data engineering learning project.",
    ),
]

# (length, source seek or card name, caption). Each section follows the approved storyboard.
SHOTS = [
    (5, "intro", "Introduction"),
    (7, 25.4, "Research discovery | Natural-language search over saved research"),
    (4, 9.0, "Refine your question | Actual recorded clarification"),
    (4, 105.0, "Date filters and sorting | Recorded offline exploration"),
    (3, 36.0, "Request activity | Actual offline fallback, not fresh ingestion"),
    (2, "pipeline-0", "Acquire"),
    (2, "pipeline-1", "Preserve raw"),
    (2, "pipeline-2", "Validate"),
    (2, "pipeline-3", "Normalize"),
    (2, "pipeline-4", "Upsert"),
    (2, "pipeline-5", "Reconcile"),
    (3, 106.0, "Explore results | Refine dates and ranking"),
    (4, 46.0, "Publication details | Authors, metadata and available abstract"),
    (4, 66.0, "Historical Gemini response | Saved replay, no new AI request"),
    (4, 77.0, "Supporting quotes | Saved evidence, not scientific truth verification"),
    (10, "closing", "Closing"),
]


def run(arguments, **kwargs):
    subprocess.run([str(value) for value in arguments], check=True, cwd=BUILD, **kwargs)


def ffmpeg_path():
    configured = os.getenv("FFMPEG") or shutil.which("ffmpeg")
    if configured:
        return str(Path(configured).resolve())
    # An optional local tool installation keeps video dependencies out of the application locks.
    sys.path.insert(0, str(ROOT / ".runtime/demo-tools"))
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def narrate():
    sys.path.insert(0, str(ROOT / ".runtime/demo-voice"))
    from piper import PiperVoice, SynthesisConfig

    voice = PiperVoice.load(str(BUILD / "en_US-ljspeech-high.onnx"))
    durations = []
    for index, (_, allotted, text) in enumerate(NARRATION):
        scale = 0.90
        for _ in range(2):
            with wave.open(str(BUILD / f"voice-{index}.wav"), "wb") as audio:
                voice.synthesize_wav(text, audio, syn_config=SynthesisConfig(length_scale=scale))
            with wave.open(str(BUILD / f"voice-{index}.wav")) as audio:
                duration = audio.getnframes() / audio.getframerate()
            if duration <= allotted:
                break
            scale *= (allotted - 0.4) / duration
        if duration > allotted:
            raise RuntimeError(f"Narration {index} exceeds its {allotted}s slot: {duration:.2f}s")
        durations.append(round(duration, 3))
        print(f"Synthesized narration {index + 1}/{len(NARRATION)}: {duration:.2f}s", flush=True)
    return durations


def main():
    if not SOURCE.is_file():
        raise FileNotFoundError("Verified showcase footage is required; see scripts/record_showcase.cjs")
    if sum(length for length, _, _ in SHOTS) != 60:
        raise ValueError("Storyboard must total 60 seconds")
    source_digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    provenance = json.loads((ROOT / "showcase/capture-manifest.json").read_text(encoding="utf-8"))
    if provenance.get("paid_provider_attempts") != 0 or provenance.get("saved_claims", 0) == 0:
        raise ValueError("Source capture lacks expected offline/replay provenance")
    BUILD.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "screenshots").mkdir(parents=True, exist_ok=True)
    ffmpeg = ffmpeg_path()
    font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/segoeui.ttf"
    shutil.copyfile(font, BUILD / "font.ttf")
    run([shutil.which("node"), ROOT / "scripts/render_demo_cards.cjs", BUILD])
    voice_durations = narrate()
    encoder = ["-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p"]
    segments = []
    for index, (length, source, caption) in enumerate(SHOTS):
        destination = BUILD / f"shot-{index:02}.mp4"
        arguments = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
        if isinstance(source, str):
            arguments += ["-loop", "1", "-framerate", "30", "-i", BUILD / f"{source}.png"]
            filters = "scale=1920:1080,setsar=1"
        else:
            arguments += ["-ss", source, "-i", SOURCE]
            (BUILD / f"caption-{index}.txt").write_text(caption, encoding="utf-8")
            filters = (
                "scale=1536:960,pad=1920:1080:192:12:color=0x081525,setsar=1,"
                f"drawtext=fontfile=font.ttf:textfile=caption-{index}.txt:fontsize=29:"
                "fontcolor=white:x=(w-text_w)/2:y=1000,"
                "drawtext=fontfile=font.ttf:text='RECORDED OFFLINE / NOV 24 CAPTURE':"
                "fontsize=19:fontcolor=0x8fa9c3:x=192:y=1045"
            )
        arguments += ["-t", length, "-an", "-vf", filters, "-r", "30", *encoder, destination]
        run(arguments)
        segments.append(destination)
        print(f"Rendered shot {index + 1}/{len(SHOTS)}", flush=True)
    (BUILD / "concat.txt").write_text("".join(f"file '{path.name}'\n" for path in segments), encoding="utf-8")
    run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            "concat.txt",
            "-c",
            "copy",
            "silent.mp4",
        ]
    )
    # Voice tracks keep natural pauses; apad fills unused time in each storyboard slot.
    audio_args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", "silent.mp4"]
    for index in range(len(NARRATION)):
        audio_args += ["-i", f"voice-{index}.wav"]
    graph = ";".join(
        f"[{index + 1}:a]apad,atrim=duration={length},asetpts=PTS-STARTPTS[a{index}]"
        for index, (_, length, _) in enumerate(NARRATION)
    )
    graph += ";" + "".join(f"[a{index}]" for index in range(len(NARRATION))) + "concat=n=5:v=0:a=1[voice]"
    video = OUTPUT / "research-navigator-60s.mp4"
    run(
        [
            *audio_args,
            "-filter_complex",
            graph,
            "-map",
            "0:v",
            "-map",
            "[voice]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-t",
            "60",
            "-movflags",
            "+faststart",
            video,
        ]
    )
    # A full decode catches corrupt frames and missing/truncated audio/video streams.
    run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", video, "-map", "0:v", "-map", "0:a", "-f", "null", "-"])
    for second, name in [
        (2, "introduction"),
        (10, "discovery"),
        (28, "pipeline"),
        (39, "details"),
        (47, "evidence"),
        (55, "closing"),
    ]:
        run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                second,
                "-i",
                video,
                "-frames:v",
                "1",
                OUTPUT / "screenshots" / f"{name}.png",
            ]
        )
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != source_digest:
        raise RuntimeError("Original footage changed")
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": 60,
        "resolution": "1920x1080",
        "fps": 30,
        "source_video_sha256": source_digest,
        "source_capture_date": provenance["recorded_at"],
        "source_research_snapshot_sha256": provenance["source_sha256"],
        "media_sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
        "narration": "Locally synthesized Piper LJ Speech voice; no paid API calls",
        "voice_segment_seconds": voice_durations,
        "paid_provider_attempts_this_build": 0,
        "full_audio_video_decode": "PASS",
        "source_footage_unchanged": True,
        "live_demo_url": None,
        "shots": [{"seconds": length, "source": source, "caption": caption} for length, source, caption in SHOTS],
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    subtitles = []
    for index, (start, length, text) in enumerate(NARRATION, 1):

        def stamp(seconds):
            return f"00:{seconds // 60:02}:{seconds % 60:02},000"

        subtitles.append(f"{index}\n{stamp(start)} --> {stamp(start + length)}\n{text}\n")
    (OUTPUT / "narration.srt").write_text("\n".join(subtitles), encoding="utf-8")
    (OUTPUT / "index.html").write_text(
        """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Research Navigator / 60-second demo</title>
<style>body{font:18px/1.6 system-ui;background:#081525;color:#eff7ff;max-width:1200px;margin:40px auto;padding:24px}
video{width:100%;border-radius:16px}a{color:#6de5cf}</style><h1>Research Navigator</h1>
<p>60-second narrated demo. Recorded offline footage and a backend architecture illustration.</p>
<video controls preload="metadata" poster="screenshots/introduction.png"><source src="research-navigator-60s.mp4" type="video/mp4"></video>
<p>The saved Gemini explanation is historical replay. No new source or LLM calls were made.</p>
<p><a href="research-navigator-60s.mp4">Download MP4</a> · <a href="narration.srt">Transcript/subtitles</a> ·
<a href="manifest.json">Capture provenance</a> · <a href="https://github.com/Sharan099/Research-Navigator">GitHub</a></p></html>""",
        encoding="utf-8",
    )
    print(json.dumps({"video": str(video), "full_decode": "PASS", "seconds": 60}), flush=True)


if __name__ == "__main__":
    main()
