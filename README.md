# Clearframe

Local Windows video watermark remover powered by ProPainter. Draw rectangles over the watermark, inspect a temporal AI preview, and export at the source dimensions. There is no blur/cover removal fallback.

## Start

Double-click **Clearframe.exe** in this folder. **Start Clearframe.cmd** is an alternative launcher. Keep this folder at its current location: the application uses its private .venv, .runtime, models, and vendor directories. Run Setup.ps1 again if relocating the runtime. Nothing needs to be installed globally. Setup is already complete on this PC.

For a fresh Git checkout, run Setup.ps1 first from PowerShell. Setup downloads the private runtime and official AI dependencies, recreates the local demo, and builds Clearframe.exe. Install Git, Python (for the initial bootstrap), FFmpeg/FFprobe, and an NVIDIA driver before the first setup. These generated files are deliberately excluded from version control.

1. Open a video, or drag a local file into the window.
2. Drag over the watermark. Hold Shift for a square. Include edges, glow, and shadow using Safety margin. The dotted boundary is the actual permitted repair area, aligned to color samples.
3. Use Move / resize to adjust the box. Add more areas if needed. Timing & motion lets you set start/end times and position keyframes; positions interpolate between keys.
4. Click Preview from playhead. Compare at normal speed and at 100% pixels; drag the green divider. Middle-drag pans a zoomed view. Preview settings match export settings.
5. Choose an output file and click Remove watermark & export. The original file is never overwritten.

The sample button loads a short moving-background clip with a preselected watermark. examples/Repaired sample.mkv is a real GPU repair, and examples/comparison.png shows a frame before and after.

## Output quality

**High quality (MP4/H.264)** is the normal export. It defaults to CRF 18 and a slow software encoder for practical file sizes while retaining source dimensions and presentation timing. It adds some compression loss across the frame. Advanced settings expose CRF: lower values retain more detail at larger sizes. File size depends on the content and need not match the input.

**Lossless master (MKV/FFV1)** is an optional archival export. It adds no lossy compression to the composited native frames. On validated YUV sources, untouched native samples outside the repair stay exactly equal to the decoded input. It can be many times larger than the source; VLC and FFmpeg-based players support FFV1. Codec format conversions for RGB masters are lossless channel repacking.

Repair uses source-scale crops, optical-flow completion, background propagation, and temporal refinement. The application does not shrink a 4K video to 720p for inference. GPU pressure triggers shorter overlapping temporal windows, not reduced spatial resolution. Very large selections can still exceed GPU capacity; reduce Context or use a tighter selection.

Match surrounding light is enabled by default. A gradient-domain boundary correction removes the model's brightness/color mismatch at the edge of the repair, which otherwise can expose a rectangular patch. It preserves the model's local detail instead of applying Gaussian blur or a broad feathered overlay. Keep a safety margin around the logo so the correction uses clean boundary pixels.

An opaque watermark permanently hides information. When clean detail is never visible in other frames, AI estimates it; fine text, faces, repetitive texture, fast motion, and large masks may show artifacts or softness. No application can guarantee exact reconstruction of all concealed content. Inspect a preview before a long export.

## Supported media

The media layer uses FFmpeg/PyAV rather than browser codec support. Common MP4, MOV, MKV, AVI, WebM, M4V, and TS containers are accepted when they contain a validated codec and pixel format. Initial support is **progressive 8-bit SDR**, including standard rotations, portrait footage, fractional frame rates, and VFR. HDR, high-bit-depth formats, interlaced video, and alpha channels are explicitly rejected rather than silently degraded.

The video stream is re-encoded once. Original compatible audio and subtitles are copied. When a container cannot preserve a subtitle/audio codec, export stops with an explanation; try MKV. Standard metadata and chapters are copied, but specialized camera side data, Dolby Vision, attachments, and secondary video streams are not preservation guarantees. Source files are always retained.

## Jobs, storage, and performance

Completed repair chunks are cached under workspace/jobs by source fingerprint and repair settings. Cancel, then start the same job to resume. Changing export compression, destination, or the report checkbox can reuse a repair. Encoding restarts coherently from the cached repair.

Processing reports are kept inside this application folder under workspace/logs. Normal exports create only the video in the chosen output folder. To also save a JSON report next to the video, enable Save processing report in the Export panel (off by default). Previews keep reports internal even when that option is enabled. Workspace/requests contains worker logs for troubleshooting.

Working folder is configurable in Advanced settings, useful for large projects or another drive. Allow room for the repair cache and a temporary encoded video in addition to the final output. Lossless masters can be much larger than compressed inputs. Cache files are retained to support resume; remove old project cache folders when they are no longer needed and no job is running.

The bundled benchmark is deliberately small: 24 frames, 640 x 360, one 108 x 32 watermark. It measured about 11 seconds on the RTX 3060, and verified exact native samples outside the repair. See examples/benchmark.json. This is not a speed or quality promise for full-length videos, larger regions, or other hardware.

## Setup / development

If dependencies are missing, run Setup.ps1 in PowerShell. It creates a local Python 3.11 runtime, installs pinned CUDA PyTorch and UI/video packages, fetches the pinned ProPainter source, and downloads official model weights. It needs network access only during setup. FFmpeg/FFprobe and a compatible NVIDIA driver must be available. The application needs no cloud API keys and makes no video uploads.

Run the GUI with .venv/Scripts/python.exe run.py. A saved selection JSON can be processed with .venv/Scripts/python.exe -m watermark_remover.worker path/to/job.json. Run tests with .venv/Scripts/python.exe -m pytest tests -q --basetemp=workspace/test-run-NEW, using a new scratch directory. Run tools/benchmark.py to recreate and measure the demonstration. The benchmark uses real CUDA inference; media tests isolate timeline/compositing correctness using a controlled test engine.

This is a personal, noncommercial application. See THIRD_PARTY_NOTICES.md and vendor/ProPainter/LICENSE.

## Version control

The repository tracks application source, tests, setup/build scripts, documentation, and the icon. It excludes Python environments, downloaded third-party code/models, videos, job caches, logs, reports, and machine-specific example projects.

The v1.1.1 tag marks the stable baseline for simpler watermark projects. Use main for the accepted working version and codex/ branches for improvements. Commit related changes together with a short explanation, then push the branch for review. Keep the separate Stable v1.1.1 application folder as a runnable local backup; Git history stores the reproducible source rather than the multi-gigabyte runtime.
