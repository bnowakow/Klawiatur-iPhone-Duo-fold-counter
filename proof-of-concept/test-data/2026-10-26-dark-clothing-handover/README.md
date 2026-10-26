# Reviewed 07:00 clothing transition, 26 October 2026

Original saved frames and SHA-256 hashes are listed in `manifest.json`.
Times below use Europe/Warsaw. The user confirmed a presenter switch.
The earlier garment has a large white/purple print; the later garment is
plain black. These are clothing cues, not identity recognition.

The last reviewed old frame is 06:59:32.059619 (counter 65,980); the first
reviewed new frame is 07:00:26.067856 (66,003). The intervening 23 folds
cannot be assigned to either stint from these samples. No exact physical
stop time or empty-chair interval was verified. The next accepted increase
is at 07:02:15.347474 (66,122); the intermediate OCR sample was rejected.

`previous-layouts.json` preserves the profiles used when the transition was
missed. The original shoulder crop stayed dark across both garments.
Supplemental clothing crops now sample the shirt print, with the existing
threshold and 60-second persistence. Regression tests include old-shirt
poses and both main/inset views. Similar garments and lighting changes
remain limitations; hourly scheduling does not establish a handover.
