"""Edit the live recording into an exactly 60-second 1080p MP4.

Input (from scripts/record_live_demo.cjs, in .runtime/live-demo/): raw.webm, timeline.json,
frame.png, intro.png, outro.png. Backend waits longer than MAX_WAIT seconds are sped up; every
other moment plays at real speed. Nothing is re-ordered, so the edit never changes what happened.

    python scripts/build_live_demo.py [--out showcase/demo-60s/research-navigator-60s.mp4]
"""

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".runtime" / "live-demo"
TOTAL, INTRO, MAX_WAIT, FPS = 60.0, 4.0, 2.0, 30


def segments(timeline, duration):
    """(start, end, speed) covering the recording from the first frame of the app to the end."""
    waits = [w for w in timeline["waits"] if w["label"] not in {"start", "end"}]
    cursor = next(w["start"] for w in timeline["waits"] if w["label"] == "start")
    end = min(duration, next(w["end"] for w in timeline["waits"] if w["label"] == "end") + 0.5)
    parts = []
    for wait in waits:
        parts.append((cursor, wait["start"], 1.0))
        length = wait["end"] - wait["start"]
        parts.append((wait["start"], wait["end"], max(1.0, length / MAX_WAIT)))
        cursor = wait["end"]
    parts.append((cursor, end, 1.0))
    return [p for p in parts if p[1] - p[0] > 0.05]


def build(out):
    timeline = json.loads((WORK / "timeline.json").read_text())
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(WORK / "raw.webm")],
        capture_output=True,
        text=True,
        check=True,
    )
    parts = segments(timeline, float(probe.stdout))
    content = sum((b - a) / s for a, b, s in parts)
    outro = TOTAL - INTRO - content
    if outro < 3:
        raise SystemExit(f"Recording too long for 60s: content is {content:.1f}s")
    chains = [
        f"[0:v]trim={a:.3f}:{b:.3f},setpts=(PTS-STARTPTS)/{s:.4f},fps={FPS}[s{i}]" for i, (a, b, s) in enumerate(parts)
    ]
    joined = "".join(f"[s{i}]" for i in range(len(parts)))
    graph = ";".join(
        chains
        + [
            f"{joined}concat=n={len(parts)}:v=1:a=0[app]",
            "[1:v][app]overlay=0:80:shortest=1,format=yuv420p,setsar=1[main]",
            f"[2:v]loop=-1:1,trim=0:{INTRO},fps={FPS},format=yuv420p,fade=out:st={INTRO - 0.4}:d=0.4,setsar=1[intro]",
            f"[3:v]loop=-1:1,trim=0:{outro:.3f},fps={FPS},format=yuv420p,fade=in:d=0.4,setsar=1[outro]",
            "[intro][main][outro]concat=n=3:v=1:a=0[v]",
        ]
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(WORK / "raw.webm"),
            "-loop",
            "1",
            "-i",
            str(WORK / "frame.png"),
            "-i",
            str(WORK / "intro.png"),
            "-i",
            str(WORK / "outro.png"),
            "-filter_complex",
            graph,
            "-map",
            "[v]",
            "-t",
            str(TOTAL),
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "24",
            "-movflags",
            "+faststart",
            str(out),
        ],
        check=True,
    )
    print(
        json.dumps(
            {
                "output": str(out),
                "content_seconds": round(content, 2),
                "outro_seconds": round(outro, 2),
                "segments": [[round(a, 2), round(b, 2), round(s, 2)] for a, b, s in parts],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "showcase/demo-60s/research-navigator-60s.mp4")
    build(parser.parse_args().out)
