# QueryDesk business walkthrough video

Audience: business and BI users. Language: English. Format: 1920×1080 landscape MP4, 24 fps, synthetic English narration. The video covers the business problem, product approach, query flow, animated architecture, governance and an actual browser demonstration.

Generated video files are intentionally excluded from this repository. The authoring sources, captions and synthetic preview images are retained; rebuild locally using the instructions below.

## Local deliverables

- `QueryDesk_business_walkthrough.mp4`: finished narrated video.
- `QueryDesk_captions.srt`: optional English subtitles; load alongside the MP4 in a compatible player.
- `chapters.txt`: chapter timestamps.
- `narration_script.md`: full spoken script and demo disclosure.
- `poster.png` / `storyboard.png`: cover and visual chapter preview.
- `plan.json`, `narrate.ps1`, `capture_demo.cjs`, `demo_server.py`, `render_video.py`: editable authoring sources.

The recording uses scripted model handoffs, the real LangGraph workflow, authenticated UI, SQL/table approvals and protected SQLite execution. It is **not** a recording of live model-provider inference or a remote warehouse connection. On-screen labels and narration disclose this distinction. The sample report uses eight tables and returns ten segment/region rows covering twenty-two non-cancelled orders. The demonstration creates a temporary isolated database and administrator; it does not alter the real app's accounts, database, secrets or provider approvals.

## Rebuild on Windows

Requires the existing application environment and frontend build, Playwright Chromium, and the installed Windows Microsoft Zira Desktop voice. Run from the workspace root:

```powershell
.\langgraph_impl\.venv\Scripts\python.exe -m pip install --target application_video/.tools -r application_video/requirements-video.txt
.\application_video\narrate.ps1
node application_video/capture_demo.cjs
.\langgraph_impl\.venv\Scripts\python.exe -X utf8 application_video/render_video.py
```

The capture script starts isolated loopback services on ports 8217 and 3217 and closes them afterward. Voice generation and rendering are local. The media tool dependencies live outside application source and are separate from application runtime requirements.

The renderer reuses existing files in `render/`; after editing a scene, remove only that scene's generated MP4 to force its regeneration. Rerun the browser capture after changing its flow or narration timing. Demo footage is regenerated each render. No stock music, stock footage or third-party voice service is used.
