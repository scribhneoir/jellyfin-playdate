Roobert bitmap fonts from the Playdate SDK 3.1.1 (`Resources/Fonts/Roobert`).
The 20 px face is used for headings, and 10 px bold for metadata and controls.
Body text uses the built-in Playdate system font. Keep each `.fnt` with its
matching image table. `make client` and `make ui-preview` copy these four assets
from your installed SDK before compiling; the source repository excludes the
SDK font files. For standalone test runners, run `make client-fonts` first.
The compiled app bundles the fonts, so no font download is needed on the device.
