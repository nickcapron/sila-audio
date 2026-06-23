# SILA branding

Starting brand assets, matching the in-app aurora identity. These are *starting
points* — refine in a vector editor (Figma / Illustrator / Inkscape) and outline
the text so the logo doesn't depend on a system font.

## Palette (the "aurora")

| Role        | Hex       |
|-------------|-----------|
| Aurora teal | `#34e3c4` |
| Aurora cyan | `#5fd0e0` |
| Aurora violet | `#8b6cf0` |
| Background  | `#0a0e14` |
| Text        | `#d7e3e8` |

Aurora gradient (used for the logo + header hairline):
`linear-gradient(100deg, #34e3c4 0%, #5fd0e0 45%, #8b6cf0 100%)`

## Files

- **`sila-logo.svg`** — horizontal wordmark (480×160). Use for the site header,
  video intro/outro, README.
- **`sila-icon.svg`** — square app/plugin icon, vector source (256×256).
- **`sila-icon.png`** — rasterized 1024×1024 app icon, **wired into the build**
  via `ICON_BIG`/`ICON_SMALL` in `vst/CMakeLists.txt` (shows on the Standalone app
  and in plugin lists). Regenerate it if you change the source.

## Plugin / Standalone icon

`sila-icon.png` is already wired in `juce_add_plugin`:

```cmake
ICON_BIG   "${CMAKE_CURRENT_SOURCE_DIR}/branding/sila-icon.png"
ICON_SMALL "${CMAKE_CURRENT_SOURCE_DIR}/branding/sila-icon.png"
```

To redesign it, edit `sila-icon.svg` and rasterize to `sila-icon.png` (Inkscape:
`inkscape sila-icon.svg -w 1024 -h 1024 -o sila-icon.png`), then rebuild.

## TODO before a public release / videos

- Outline the wordmark text to a path (font-independence).
- Export PNG icons (512/256/128/64) and wire `ICON_BIG`/`ICON_SMALL`.
- A 1280×720 title card for the build videos (wordmark on the aurora background).
