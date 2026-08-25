# Probe and calibration scenes

The developer cockpit lists every subdirectory here in its target picker as
`probe:<name>`, alongside the now-playing wallpaper. A probe is just a normal
wallpaper folder: a `project.json` plus whatever assets it references. Pick one
as the target, then start the bench to run it under the daemon standdown with the
instruments and isolation flags you have set.

Use these for repeatable parity checks: a scene that isolates one behavior (a
single light, one particle system, a known-good reference frame) so you can
flip a fix on and off and see the before and after against a fixed input,
instead of chasing the effect inside a busy production wallpaper.

## Layout

```
probes/
  my-light-check/
    project.json
    scene.json
    materials/ ...
  particle-emit/
    project.json
    ...
```

## Notes

- This folder ships empty. Add scenes locally, or bundle a curated set at
  release time.
- Anything bundled for public release must be clean-room content the project
  is licensed to distribute. Do not commit scenes derived from third-party
  workshop assets.
- Names become the picker label, so keep them short and descriptive.
