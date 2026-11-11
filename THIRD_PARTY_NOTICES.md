# Credits and third-party components

Clearframe runs ProPainter by Shangchen Zhou, Chongyi Li, Kelvin C. K. Chan, and Chen Change Loy (S-Lab, Nanyang Technological University). Source is pinned to commit 326782529a63d997cab80ab21439b92ed422b3d3, with official release v0.1.0 weights.

- ProPainter: https://github.com/sczhou/ProPainter — S-Lab License 1.0, noncommercial use. The full upstream license is in vendor/ProPainter/LICENSE. The weights are downloaded from the authors' release, with local hashes recorded in models/manifest.json.
- RAFT implementation and model are included in the upstream ProPainter distribution. Retain the upstream notices and attribution. See vendor/ProPainter/RAFT.
- PyTorch and Torchvision: https://pytorch.org — BSD-style licenses and bundled third-party notices; see .venv/Lib/site-packages/torch and torchvision.
- PySide6 / Qt for Python: https://doc.qt.io/qtforpython-6/ — LGPLv3/GPLv3/commercial licensing as applicable. PySide6 remains a dynamically loaded, replaceable Python dependency. Its wheel includes license texts.
- PyAV: https://pyav.org — BSD license; its FFmpeg libraries carry their own licenses.
- FFmpeg: https://ffmpeg.org — the installed build uses GPL components. This app invokes the existing system executable. It does not bundle or redistribute that executable.
- NumPy, SciPy, OpenCV, Pillow, einops, tqdm, scikit-image, pytest, and their dependencies: see the respective installed package license files.
- The included example shot is derived from the bmx-trees sample distributed with ProPainter (DAVIS footage); it is included here solely as a personal, noncommercial demonstration. Keep it separate from commercial redistribution and retain upstream dataset attribution.

ProPainter's noncommercial license fits the user's stated personal use. A commercial application or service requires a fresh license review and, for ProPainter, permission from its authors. This project does not change any upstream license.
