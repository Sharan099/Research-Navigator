# Research Navigator: 60-second demo

## Current cut: live recording (November 26)

`docs/media/research-navigator-demo.mp4` replaces the earlier narrated edit described below. It is a
real browser session against a live server on a disposable database copy: natural-language search,
a follow-up refinement, opening a paper, asking the LLM to explain it, checking the cited quote, and
the activity log showing the Gemini → Groq fallback. Responses are live (OpenAlex, Groq
`openai/gpt-oss-120b` after Gemini returned 429). The only edit is speeding up backend waits longer than
two seconds. Reproduce with `node scripts/record_live_demo.cjs`, `node scripts/record_live_demo.cjs --cards`,
then `python scripts/build_live_demo.py`.

## Earlier cut (superseded)

Audience: a technical hiring manager reviewing a working-student data engineering project.
Format: 1920 × 1080 MP4, local synthesized English narration, visible captions and an SRT transcript.

## Storyboard

| Time | Picture | Message |
|---|---|---|
| 0–5s | Branded introduction | Research Navigator. From scientific publications to actionable research insights. |
| 5–12s | Actual recorded natural-language discovery | Search a research topic in plain English. This footage uses saved tactile sensing publications. |
| 12–20s | Recorded clarification, then date/sort controls | Refine the scope and choose relevant filters. |
| 20–23s | Actual recorded request timeline | Show transparent processing and the honest offline fallback. No fresh ingestion is implied. |
| 23–35s | Six highlighted architecture steps | Acquire → preserve raw → validate → normalize → upsert → reconcile. This is an architecture illustration. |
| 35–42s | Recorded filtering and publication details | Explore dates, ranking, authors, metadata and the available abstract. |
| 42–50s | Genuine saved Gemini response and supporting quotes | Historical replay, not a new model response or proof of scientific truth. |
| 50–60s | Project name and GitHub address | Explore the code, architecture and reproducible evaluations. A live-demo link is omitted because deployment is unverified. |

## Truth and provenance

The edit reuses the November 24 offline screen recording and its capture manifest. It does not
record a new application session or call OpenAlex, Gemini or Groq. Historical AI output stays visibly
labeled. The architecture cards describe the backend implementation; they do not present synthetic
records, counters or an animation as successful live ingestion. The humanoid-manipulation search
from the suggested storyboard is replaced with the actual tactile-sensing query in the source footage.

The new build preserves the source video's hash, records its historical research-snapshot hash,
checks that narration fits each section, fully decodes both output streams and saves important frames.
Its manifest separates original capture date from edit date. Downloading local tools and a public
voice model is separate from application/provider access.

## Files and tools

- `scripts/build_demo.py` edits footage with FFmpeg, synthesizes narration locally with Piper,
  verifies full decoding, and writes the MP4, subtitles, player, screenshots and provenance manifest.
- `scripts/render_demo_cards.cjs` uses Playwright/Chromium to render the title, closing and architecture
  cards. It does not start or modify Research Navigator.
- Outputs: `showcase/demo-60s/`. Intermediate files, voice model and optional tools: `.runtime/`.
  Both locations remain ignored by Git. Application code and research databases are unchanged.

## Reproduce on this Windows workspace

Use the existing locked application/browser dependencies and Chromium. The media-only dependencies
are deliberately separate from the application requirements:

```text
python -m pip install --target .runtime/demo-tools imageio-ffmpeg==0.8.1 --no-deps
python -m pip install --target .runtime/demo-voice piper-tts==1.8.0 pathvalidate==3.3.1 --no-deps
```

Piper also requires `onnxruntime` and its dependencies in the Python environment; the existing
workspace already supplies them. A fresh media environment should install Piper with dependencies
instead of using `--no-deps`. The build script does not install packages or download models itself.

Download `en_US-ljspeech-high.onnx` and its `.onnx.json` configuration from the
[official voice repository](https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/ljspeech/high)
into `.runtime/demo-build/`. Keep its model card. The model card identifies the LJ Speech dataset
as public domain. Piper is a separate GPL-licensed tool; its source and model are not bundled into
the application release. See the [Piper Python interface](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/API_PYTHON.md).

Then run with the workspace dependencies available on PYTHONPATH:

```text
python scripts/build_demo.py
```

`FFMPEG` can specify a full executable; otherwise the helper finds FFmpeg or the local imageio-ffmpeg
binary. Windows Segoe UI is used only for local rendering. The original walkthrough and its capture
manifest must already exist; `scripts/record_showcase.cjs` documents capturing that prerequisite.

Open `showcase/demo-60s/index.html` or play `research-navigator-60s.mp4` directly. The SRT contains
the spoken script, and `screenshots/` contains six important frames. Media validation is separate
from backend regression results; this edit does not establish fresh API availability or deployment.

## Verified output

| Check | Actual result |
|---|---|
| Container and duration | MP4, exactly 60.00 seconds |
| Picture | H.264, 1920 × 1080, 30 fps |
| Narration | Locally generated Piper voice; AAC mono audio; all five voice sections fit their slots |
| Audio signal | FFmpeg mean volume −18.5 dB, maximum −0.3 dB; not silent |
| Full decode | Both video and audio decoded successfully |
| Chromium playback | Playback and seeking at six chapter positions passed; no page errors |
| Source integrity | Original screen-recording hash unchanged |
| Application regression | 293 Python tests passed, 2 PostgreSQL skips; JavaScript syntax and Ruff passed |
| Saved frames | Introduction, discovery, pipeline, details, evidence and closing |

Machine-readable records are `manifest.json` and `playback-validation.json` beside the MP4.
The existing Starlette/httpx deprecation warning remains; no application dependency was changed.
