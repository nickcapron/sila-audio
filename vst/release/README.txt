SILA - a Digitakt-style sampler / step sequencer (Windows x64)
=================================================================

What's in this zip
------------------
  SILA.vst3\          the plugin, for any VST3 DAW (Reaper, Ableton Live 11+, FL Studio,
                      Bitwig, Studio One, Cubase, ...)
  SILA.exe            the Standalone app - no DAW needed, just an audio device
  install-vst3.cmd    copies the plugin into the folder every DAW scans (asks for admin)

Install
-------
  Plugin:   double-click install-vst3.cmd.  (Or copy the SILA.vst3 FOLDER yourself into
            C:\Program Files\Common Files\VST3\ - the whole folder, not just the file inside.)
            Then rescan plugins in your DAW and add "SILA" as an instrument.
  Standalone: put SILA.exe anywhere and run it.

  Windows will show "Windows protected your PC" the first time, because this is a free,
  unsigned build. Click "More info", then "Run anyway". That's it.

  Requires the Microsoft Edge WebView2 Runtime (already on nearly every Windows 10/11 PC).
  If SILA opens with a message saying it's missing, follow the link in that window.

First steps
-----------
  - A new instance opens to an empty project with 8 lanes and a factory kit loaded
    (Behringer RD-6 drums + Casio CZ-1 voices). Click pads to program steps - it makes
    sound immediately.
  - PROJECTS -> "Factory Showcase" loads a complete example song. Turn on Song Mode and
    press play.
  - Press ? inside SILA for the full keyboard / mouse cheat-sheet.
  - Your samples and projects live in  %USERPROFILE%\SILA\  (library\ and projects\).
    LIBRARY -> + Import pulls in any folder of WAV/AIFF files and sorts it by type.

In a DAW
--------
  - Tempo and play/stop follow the DAW. The BPM readout in SILA is display-only there.
  - Per-lane outputs: SILA exposes a Main bus plus one stereo bus per lane, so each lane
    can go to its own DAW track / effects. In Reaper: set the SILA track to 16 channels,
    then add receives from channel pairs 3/4 (lane 1), 5/6 (lane 2), ...
  - MIDI export bounces the song (or current pattern) to a .mid file, one channel per lane.
  - Live MIDI input: MIDI channel N plays lane N. C3 is the lane's programmed pitch.

Known gaps (v1.0)
-----------------
  - No undo. Edits are immediate. Save often (PROJECTS -> Save).
  - Renaming / moving / deleting a sample in LIBRARY doesn't update projects that use it;
    reload them and they'll show a  "sample missing"  badge on that lane - click it to
    pick a replacement.
  - Windows only for now. The code builds for macOS (VST3/AU) but nobody has tested it.
