# Clearframe 1.1 validation

Checked on the user's RTX 3060 12 GiB and Ryzen 7 5700X, 12 November 2026.

## Version 1.1.1 report storage update

Normal exports now leave only the video in the selected output folder. Every completed run keeps a uniquely named processing report under workspace/logs in the application folder. The Export panel has an unchecked-by-default Save processing report option to also write a report next to a final exported video. Preview reports stay internal. Worker diagnostics remain under workspace/requests.

All 20 tests passed, including new checks for internal-only reports, optional report export, preview report placement, old-project defaults, unique internal history, and reuse of repair chunks when changing the report preference. The GUI preview and MP4 worker export also passed with the new checkbox off. The shot detector and repair algorithm were not changed in this update.

## Actual user clip revision

- Input: 15 seconds, 1440 x 2560, 24 fps, 35,560,506 bytes, with audio.
- Previous lossless partial: 379,990,624 bytes. Lossless encoding was the reason for the file growth.
- Revised MP4: 12,288,871 bytes (about 11.7 MiB), preserving 1440 x 2560, 24 fps, and 15 seconds.
- Normal export is now MP4, CRF 18, slow H.264 software encoding. Lossless remains an explicitly optional archival format.
- Boundary color matching is enabled by default. It solves a harmonic boundary correction to remove brightness/color drift at the rectangle edge; it does not apply a Gaussian blur or replace fine detail with a blurred overlay.
- Inspected input, prior repair, and actual revised compressed output at 0, 5, 10, and 14 seconds. The strong rectangular boundary visible in the first repair was substantially reduced in these frames.
- Mean sampled luma PSNR outside the repaired area: 49.67 dB over those four frames. This measures compression impact on the unedited area, not recovery accuracy behind the watermark.
- Final export verification checked every video presentation timestamp against the encoded master, frame count, dimensions, and copied audio payload hashes. All passed.

The revised output and before/after review are in workspace/quality-review. The user's source video was not modified.

## Model benchmark

Real CUDA inference was exercised on the official model weights, not a mock removal engine. A 24-frame, 640 x 360 moving-background demonstration initially completed in about 11 seconds, including export and verification. The repair-region SSIM against a clean reference improved from 0.150 to 0.908. Untouched native YUV samples outside the repair boundary were exactly equal in the lossless output. See examples/benchmark.json. This small example is not a speed or quality promise for large files or other scenes.

## Automated checks

18 media and boundary-correction tests passed after the revision:

- CFR and VFR frame timing, including temporal-window boundaries and cached resume.
- Lossless native-sample equality outside the permitted repair area.
- Audio payload copying, preview trimming, cancellation and resume.
- 1920 x 1080, 3840 x 2160, and portrait geometry, with native-scale crop assertions.
- YUV 4:2:0, 4:2:2, 4:4:4, full-range YUV, RGB, and grayscale media paths.
- Rotation retained in MP4 and MKV exports.
- Explicit rejection of unsupported high-bit-depth media.
- Harmonic color-offset correction without loss of a synthetic fine texture; a watermark inside the reference is not copied back into the repair.

The media tests use a controlled replacement engine to isolate media preservation from model behavior. GPU inference was checked separately with the real model and actual GUI-worker flow.

The GUI was rendered and reviewed, and tools/verify_ui.py exercised preview generation, the comparison view, and MP4 export with audio through the same QProcess worker used by the application. Desktop interaction was stopped when the user pressed Escape; subsequent automated checks used a separate offscreen test window.

## Limits

Opaque hidden content is estimated, so reconstruction cannot be guaranteed to match the original or look invisible in every scene. Some difficult scenes can still show softness, flicker, or incorrect texture. HDR, high bit depth, interlacing, alpha video, and malformed timelines are explicitly unsupported in this release. MP4 encoding is lossy; only the optional lossless path can preserve every unaffected decoded sample exactly. Specialized metadata and secondary video streams are not preservation guarantees.
