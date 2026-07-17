# YouTube Transcriber — Design Spec (2026-07-17)

## Goal
New CleanMint page: paste a YouTube URL, click **Transcribe**, get the video plus a
beautifully formatted transcript as `.txt` and `.pdf` in `~/Downloads`. Fully automatic.

## Decisions (user-approved)
- **Captions first**: if the video has YouTube captions (manual preferred over auto),
  use them directly — instant, no transcription.
- **Fallback**: local `faster-whisper` **medium**, int8, beam 5, VAD filter (no GPU on
  this machine; ~1–1.5× video length). One-time ~1.5 GB model download on first use.
- **Format**: plain paragraphs, no timestamps. Header block: title, channel, upload
  date, duration, URL, transcript source (captions / Whisper).
- **Outputs only**: `.txt` + `.pdf` (reportlab). No docx/srt/vtt/diarisation/summary.
- Downloaded video is kept in `~/Downloads`, never deleted.

## Files
- `cleanmint/core/transcriber.py` — all logic, UI-free:
  - `fetch_metadata(url, cookies: bool)` — `yt-dlp --dump-json` → title/channel/date/duration
  - `download_video(url, cookies)` — `yt-dlp -P ~/Downloads`, returns file path
  - `fetch_captions(url, cookies)` — `--write-subs --write-auto-subs --sub-langs "en.*"
    --sub-format json3 --skip-download`; manual > auto; returns parsed events or None
  - `parse_json3(text)` → word/segment list; de-duplicate rolling auto-caption overlap
  - `build_paragraphs(segments)` — sentences → paragraphs split on speech pauses
  - `whisper_transcribe(path, progress_cb)` — lazy-import faster-whisper; feed video
    file directly (decodes internally, no separate ffmpeg step)
  - `write_txt(...)`, `write_pdf(...)` — to `~/Downloads/<safe-title>-transcript.{txt,pdf}`
  - `sanitize_filename(title)`
- `cleanmint/ui/transcriber_page.py` — page + QThread worker (Snapshot-page pattern):
  URL box, "Use Chrome cookies" checkbox (`--cookies-from-browser chrome`),
  Transcribe button, live status log, progress bar.
- Sidebar entry in `main_window.py` (lazy-loaded like other pages).

## Rules compliance
- All subprocess calls list-form, never `shell=True`.
- No deletions anywhere; writes only new files into `~/Downloads`.
- All work in QThread; UI never blocked.

## Error handling
Bad URL / no network / video unavailable → clear message in status log.
Age-restricted → suggest enabling the cookies checkbox.
No captions and faster-whisper not installed → show install hint
(`venv/bin/pip install faster-whisper`); app must still launch without it.

## Progress reporting
- Download: parse yt-dlp `%` lines.
- Whisper: segment end-time ÷ video duration.

## Tests
Pure-function tests, no network: `parse_json3` (fixture json3 incl. auto-caption
overlap), `build_paragraphs`, `sanitize_filename`, txt/pdf writers to tmpdir.
