# Video watermark remover: research and implementation plan

Prepared 12 November 2026. Status: implemented and tested as Clearframe 1.1. See README.md and VALIDATION.md for the delivered behavior and measured results. Normal export is now high-quality MP4; lossless MKV is an optional master format.

## Decision

Build a local Windows desktop application for personal use. The user opens a video, draws a square or rectangle over a watermark, previews the repair, and exports at the source dimensions and presentation timing. Use temporal video inpainting, beginning with ProPainter, rather than a blur or cover effect. Treat the engine choice as provisional until an actual quality and memory benchmark passes on this PC.

Use Python, PySide6 for the desktop interface, a separate PyTorch/CUDA inference process, and FFmpeg/PyAV for media handling. Keep the model behind a replaceable adapter. Processing stays on the PC after the initial dependency and model downloads.

The user confirmed personal use. ProPainter permits noncommercial use under its S-Lab license; commercial use requires separate permission. [ProPainter license](https://github.com/sczhou/ProPainter/blob/main/LICENSE)

## What quality preservation means

The application will preserve source width and height, display aspect and orientation, frame count, presentation timing, and audio synchronization on supported inputs. The final-quality inference path uses source-scale pixels; a 4K input must not secretly become a 720p repair that is enlarged back to 4K.

The rectangle and its visible safety margin define the permitted repair area. Everything else is preserved in the source's decoded pixel planes before encoding. Surrounding pixels supplied as AI context are read-only context, not an expanded replacement area.

There are two export contracts:

| Export | Intended behavior | Tradeoff |
| --- | --- | --- |
| Lossless master, FFV1 in MKV | Preserve the composited decoded frames without a further lossy video encode. Validate native pixel-format handling and unchanged planes outside the repair. | Large files and more limited player support. |
| High-quality delivery, H.264 in MP4 initially | Preserve dimensions and timing with a carefully tested quality setting; start evaluation around CRF 16 with a slow software preset. | Lossy encoding can change pixels across the frame. File size may differ from the input. |

Lossless describes storage of the resulting frames, not recovery of the concealed original. An opaque watermark hides information. If no clean observation exists in another frame, the model must estimate the missing appearance. Exact faces, writing, fine texture, and large permanently covered areas may remain imperfect. Avoiding a blur filter cannot guarantee that every AI reconstruction is sharp or artifact-free. Matching source bitrate is not a quality guarantee. [FFV1 specification](https://github.com/FFmpeg/FFV1/blob/master/ffv1.md)

## Research findings and engine choice

Professional temporal fill uses surrounding footage, motion analysis, and sometimes a manually prepared clean reference frame. Adobe documents temporally aware filling from other frames and reference-frame assistance for difficult backgrounds. These are useful design principles, although Adobe does not publish its full implementation. [Adobe Content-Aware Fill](https://helpx.adobe.com/after-effects/desktop/remove-objects-from-your-videos/content-aware-fill.html)

ProPainter combines completed optical flow, image/feature propagation, and temporal attention. This makes it a strong first candidate for recovering detail that is visible elsewhere in the shot. This is a practical recommendation for this hardware, not a verified claim that it is universally the best model. [ProPainter method](https://shangchenzhou.com/projects/ProPainter/)

| Candidate | Evidence relevant to this application | Decision |
| --- | --- | --- |
| ProPainter | Official FP16 estimates for 50 frames are approximately 6 GB at 640 x 480, 7 GB at 720 x 480, and 19 GB at 1280 x 720. Actual memory depends on the settings and implementation. | First engine to benchmark, using contextual crops at native pixel scale. |
| SVOR, released April 2026 | A newer removal-specific diffusion model. Current repository estimates about 33 GB at its default settings, or 24 GB with model CPU offload. | Optional later comparison for difficult content; 12 GB operation and performance are unproven here. |
| VACE | Supports masked video editing, but its supplied preprocessing uses model-specific resolution and temporal limits. | Possible future adapter after a quality/speed comparison; its default pipeline does not meet this application's preservation contract. |
| VideoPainter | Research-oriented video inpainting with a license restricting use to academic, research, and educational purposes and prohibiting production use. | Exclude from the application backend. |

Sources: [ProPainter memory guidance](https://github.com/sczhou/ProPainter#-memory-efficient-inference), [SVOR repository](https://github.com/xiaomi-research/svor), [VACE user guide](https://github.com/ali-vilab/VACE/blob/main/UserGuide.md), [VideoPainter license](https://github.com/TencentARC/VideoPainter/blob/main/LICENSE).

The stock ProPainter script is a research demonstration, not the production media pipeline. It loads the input into memory, resizes dimensions to multiples of eight, averages overlapping predictions of the same target frame, and uses a basic imageio video export. Its temporal subvideo setting alone does not bound all input storage. Replace these behaviors with bounded ingestion, padding rather than resizing, evaluated window assembly, and controlled encoding. Same-frame prediction averaging is not a spatial blur filter, but disagreement can soften details. [Official inference source](https://raw.githubusercontent.com/sczhou/ProPainter/main/inference_propainter.py)

## Machine constraints

Read-only inspection found:

- NVIDIA GeForce RTX 3060 with 12 GiB VRAM.
- AMD Ryzen 7 5700X, 8 cores and 16 logical processors; approximately 32 GiB available to the runtime as system capacity.
- FFmpeg and FFprobe 9.0.1 already installed, including FFV1 and H.264 encoders.
- Python 3.14.7 is installed; PyTorch and PySide6 were not found in that interpreter.
- Approximately 26 GiB free on C: and 503 GiB on D: at inspection time.

Plan an isolated, tested Python environment, initially targeting Python 3.10 or 3.11 rather than assuming compatibility with the existing 3.14 installation. Pin the actual dependency versions and upstream commit after a successful CUDA inference test. Avoid changing the user's global Python environment.

Twelve GiB is suitable for evaluating moderate native-scale crops, not a promise of full-frame 720p or 4K temporal inference. Start calibration around a 640 x 480 or comparable context area, then select temporal window sizes using measured peak memory with headroom. Larger masks require larger context, shorter windows, or a clearly reported hardware limit. Preserve quality settings rather than silently reducing spatial resolution. Exact processing speed must be measured.

Use bounded frame buffers and crop caches. Do not extract the entire video into full-resolution PNG files. Check working and output disk space before a job, allow a configurable cache/output location, and estimate the size of large lossless masters. D: is a possible location to select during execution; no files have been written there.

## User workflow

1. Open or drag in a video. Show its resolution, duration, and support status.
2. Scrub or step through frames and draw a rectangle. Hold Shift for a square; move, resize, zoom, and undo the selection.
3. Apply the region to the whole video or a chosen time interval. Support additional rectangles. Screen-fixed watermarks use a fixed rectangle; position/size keyframes support watermarks that move. Automatic object tracking is a later enhancement.
4. Display a small adjustable safety margin so antialiasing, glow, or shadow around the watermark is included explicitly.
5. Generate a short preview using the same source-scale repair settings as export. Offer synchronized before/after playback, a comparison slider, and a 100% crop view. A smaller display proxy is allowed, but must not substitute for production inference.
6. Export a lossless master or a high-quality delivery file. Show progress, estimated remaining time after calibration, output location, and cancel/resume controls. Keep the original file intact.

## Processing design

### 1. Inspect the source

Use FFprobe to inspect the actual container and codecs rather than checking only filename extensions. Record all streams, dimensions, pixel format, bit depth, sample aspect, rotation/display matrix, color range/primaries/transfer/matrix, time bases, frame presentation timestamps, stream start offsets, and chapters where applicable. Detect scene cuts and build a seek index as needed. [FFprobe documentation](https://www.ffmpeg.org/ffprobe.html)

Target common FFmpeg-decodable local formats: MP4, MOV, MKV, AVI, WebM, M4V, and MPEG-TS with tested codecs. Container support does not mean every possible codec/profile inside it is supported. Encrypted, corrupt, or unvalidated inputs get a specific explanation.

The initial shipping gate covers progressive 8-bit SDR footage, including variable frame rate, portrait orientation, and rotated video. Add 10-bit SDR and HDR only after the color path passes explicit tests. Interlaced media, alpha-bearing video, unusual display transforms, and specialized side data require validation before claiming preservation support. Do not silently deinterlace, discard alpha, or treat all high-bit-depth video as SDR.

### 2. Map the rectangle correctly

Store selections in source pixel coordinates and time intervals. Map the displayed selection through zoom, letterboxing, sample aspect, and rotation. Clamp to valid image bounds. Include explicit chroma-sample alignment in the repair guard band for subsampled YUV sources; display the actual permitted edit boundary.

### 3. Recover the missing content over time

For each shot, extract a stable crop containing the repair area plus enough surrounding context. Keep one source pixel per inference pixel and pad to the model's required dimensions. Include past/future frames and selected references from the same shot. Split at cuts so unrelated scenes are never used as donors.

Run optical flow, flow completion, propagation, and model refinement in bounded overlapping windows. Compare the stock same-frame averaging against retaining predictions from the center of each window. Select the policy from measured detail and temporal stability. Include chunk boundaries in visual review. Reuse useful reference/crop data without keeping an entire long video in GPU memory.

If a tight crop misses useful background, expand context and retry within the memory budget. Reduce temporal batch/window pressure first when memory is insufficient, and expose any consequential change. If the mask cannot be repaired at source scale within available resources, report that limitation. A separately labeled draft mode can eventually use smaller inference, but it cannot be described as full-quality native repair.

### 4. Composite into native frames

Retain the original decoded pixel planes. Convert only the model crop to the model's working color space. Convert the repaired result back and insert it only inside the permitted repair area. Preserve native planes everywhere else; avoid a full-frame RGB/YUV round trip. Evaluate seams and color matching at the boundary without using a spatial blur as the removal method.

HDR requires a tested scene/color-managed path for PQ or HLG, luminance, gamut, and source bit depth. Copying HDR tags onto an ordinary 8-bit model result does not preserve HDR. Keep this as an explicit feasibility gate and report unsupported HDR until validated. [FFmpeg color conversion documentation](https://ffmpeg.org/ffmpeg-filters.html#zscale)

### 5. Encode once and preserve the timeline

Carry integer presentation timestamps with rational time bases and durations through decode, inference, and encode. Do not rebuild timing from an average frame rate. In particular, do not feed variable-rate frames into a raw-video pipe at one assumed FPS. Validate time-base rescaling and muxer rounding. [PyAV time documentation](https://github.com/PyAV-Org/PyAV/blob/master/docs/api/time.rst)

The edited video stream must be decoded and re-encoded. Copy compatible original audio/subtitle streams and preserve their relative start offsets. Validate container compatibility; offer MKV when MP4 cannot retain a track rather than silently omitting it. Preserve supported metadata and chapters; explicitly report metadata that cannot survive export. [FFmpeg stream copy and stream selection](https://www.ffmpeg.org/ffmpeg.html)

Write to a new temporary output, check decoding and expected media properties, then finalize the output. Store a job manifest with source fingerprint, selections, engine/settings, and completed chunks to support cancellation and compatible resume. Cache repaired frames/crops with their exact timestamps and durations, then perform a coherent final encode/remux; do not resume by concatenating arbitrary partially encoded segments with incompatible GOP or timestamp boundaries.

## Implementation stages and acceptance gates

| Stage | Concrete deliverable | Acceptance gate |
| --- | --- | --- |
| 1. Feasibility and engine benchmark | Isolated environment, reproducible CUDA inference, native-crop prototype, measured quality and resource report. | ProPainter repairs representative short clips at source pixel scale on this RTX 3060; no unexplained geometry/timing changes. Resolve dependency or model limitations before building the full interface. |
| 2. Preservation pipeline | Timestamp-aware media reader/writer, native-plane compositor, lossless and delivery exports, stream remux. | Pass outside-area equality for the lossless path, timing/A-V checks, and codec/orientation fixtures. |
| 3. Rectangle editor and preview | Native desktop window, source-coordinate selections, time ranges/keyframes, full-quality comparison preview. | Drawn coordinates match the actual repair on landscape, portrait, rotated, and zoomed displays. |
| 4. Full-job processing | Bounded windows, scene boundaries, progress, cancellation/resume, disk and OOM handling. | Long jobs keep RAM/VRAM bounded; resumed output is consistent; failures preserve original files and completed work. |
| 5. Packaging and polish | Documented launcher/installer, dependency/model handling, licenses, settings, final regression report. | Application launches from its documented entry point without manual console steps and passes the agreed quality corpus. |

The first stage determines defensible speed estimates, default crop/window settings, and whether an additional engine is justified. Do not train a new model or commit to a large diffusion dependency before this evidence exists. Optional later work includes clean-reference-frame assistance, an independently benchmarked diffusion adapter, automatic tracking, and additional export formats.

## Verification plan

Create clean reference clips and overlay synthetic opaque and translucent watermarks. This provides ground truth that real watermarked clips do not provide. Include walls, foliage, water, fine lines, existing text, faces moving behind the mark, pans, fast motion, cuts, and corner/edge placements. Include both small and large selections. Compress marked sources realistically rather than testing only ideal overlays.

Evaluate the repaired region separately from the unchanged background. Use ROI PSNR/SSIM and a perceptual metric, flow-aligned temporal error, and manual inspection at normal speed and 100% zoom. Check flicker, softness, ghosting, residual logos, seams, and temporal-window boundaries. Use an unedited export through the same media path as a baseline to separate encoding loss from repair errors. Numerical scores alone do not establish an invisible repair.

Media checks include exact raster dimensions, display geometry, frame count and frame presentation schedule within the output time-base precision, duration, and A/V synchronization. Compare native decoded planes outside the repair for the lossless output, and original packet payload hashes for copied audio where appropriate. Test fractional CFR such as 30000/1001, phone VFR, nonzero stream offsets, rotation, multiple tracks, and missing audio.

Exercise 720p, 1080p, and 4K sources with H.264, HEVC, AV1, and VP9 where supported, plus the separately gated color formats. Measure peak RAM/VRAM, elapsed time, temporary storage, memory-pressure recovery, cancellation, resume, and disk exhaustion. Performance and visual quality thresholds will be set from the feasibility samples; unsupported cases must not be presented as successful quality-preserving exports.

## Current boundary

The application has been implemented with its own runtime, official ProPainter weights, a native rectangle editor, comparison preview, resumable jobs, and verified exports. After the user's first test, version 1.1 changed the normal export to CRF 18 MP4 and added harmonic boundary color matching to reduce the visible repair rectangle. The user's 1440 x 2560, 24 fps test clip was re-exported successfully at 12,288,871 bytes, with its audio payload preserved. See VALIDATION.md for the checks and limits.
