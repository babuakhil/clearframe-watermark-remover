# Local demonstration files

Videos, generated comparisons, processing reports, and machine-specific selection files are kept locally and excluded from Git.

Setup.ps1 recreates the demonstration from the pinned upstream ProPainter sample frames when it is missing. It does not upload footage. To recreate it manually after setup:

```powershell
.venv/Scripts/python.exe -c "from tools.benchmark import make_examples; make_examples()"
```

The source shot is part of the upstream ProPainter/DAVIS example material. Retain its attribution and use it only as a personal, noncommercial demonstration; see THIRD_PARTY_NOTICES.md.
