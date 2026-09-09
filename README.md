# Project Whisper

A private, local transcription workspace for macOS. It uses the multilingual
Hugging Face Whisper Large v3 Turbo model for English and Chinese speech recognition and a Core ML
speaker-diarization pipeline for labels such as `Speaker 1`, `Speaker 2`, and
`Speaker 3`.

## Use it

1. Put recordings in `01 Audio Inbox`, leave them anywhere on your Mac, or copy a YouTube/Bilibili video link.
2. Double-click `Transcribe Audio.app`.
3. Drop or choose one or more recordings, or paste one or more YouTube or Bilibili links into the app.
4. Choose **Automatic**, **English**, or **中文 (Chinese)** for the language.
5. Keep **Automatic (1–3)** unless you know the exact number of speakers.
6. Add the work to the task manager and select **Start Queue**.

The task manager keeps every source visible with its current step, percentage,
and estimated finishing time. On this 36 GB M3 Pro MacBook Pro, up to four jobs
run simultaneously; any additional jobs wait in the queue automatically.

Results appear under `02 Transcripts` using the submission date and safe title,
for example `20260909_中文_video_title/20260909_中文_video_title.md`. The app
preserves valid Unicode, safely shortens very long names, substitutes `video`
for unusably short or unsupported titles, and adds a video ID or number rather
than overwriting a collision. Each package contains readable Markdown, plain
text, SRT subtitles, and detailed JSON. Select
**Open Latest Transcript** when the job finishes, or **Show All Results** to
open the complete results folder. Recordings that already have results are not
shown as new work when the app starts again.

The first run downloads approximately 2 GB of model data. Later runs reuse the
local copies. Transcription is processed on this Mac and is not sent to an API.
When you use a YouTube or Bilibili link, the app downloads that video's audio
before processing it locally. Public single-video links are supported; videos
that require login, paid access, DRM, or unavailable regional access may be
rejected by the source site.

## ChatGPT and Codex

The personal plugin is named `mungbai-whisper`. In a new ChatGPT desktop or
Codex conversation, you can say:

> Transcribe these links for me: <link 1> <link 2> ...

The `transcribe` tool accepts any number of local media paths, YouTube links,
and Bilibili links in one request. Up to four jobs run concurrently on this Mac;
additional jobs remain queued and start when a worker is free. It reports
step-by-step progress and an estimated remaining time, saves the results under
`02 Transcripts`, and returns transcript text to the conversation. Very long
transcripts are returned in sections through the `read_transcript` tool.

The MCP bridge runs locally and does not provide general-purpose web downloads,
browser control, or file deletion. It only reads supported local media, fetches
audio from supported video links, writes transcript packages, lists recent
results, and reads those saved transcript files.

On this MacBook, the completed recommended-quality runs took about **2 minutes
8 seconds for an 18-minute recording** and **3 minutes 16 seconds for a
29-minute recording**. Actual time varies with audio quality and background
noise, but these measurements leave comfortable room inside a 15-minute target
for a typical 20-minute recording.

## Modes

- **Recommended quality** uses `mlx-community/whisper-large-v3-turbo`.
- **Faster transcription** uses the smaller multilingual Whisper model when
  turnaround time matters more than maximum accuracy.

Speaker names are intentionally generic and local to each recording. For
example, `Speaker 1` in one transcript is not assumed to be the same person as
`Speaker 1` in another transcript.

## Folders

- `01 Audio Inbox` — recordings waiting to be processed.
- `02 Transcripts` — completed transcript packages.
- `Application` — project-owned source, tests, and configuration.
- `Library` — the isolated Python runtime, model cache, compiled tools, and
  third-party source.
- `Logs` — diagnostic files if a recording cannot be processed.

Existing transcripts are reused when the source recording has not changed.
Dropping the same file into the app again therefore does not waste processing
time.
