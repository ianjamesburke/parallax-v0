# Parallax Plan YAML — Agent Reference

This file documents the plan.yaml fields available to agents and operators.
Fields marked **required** have no default and must be present. All others are optional.

---

## Top-level fields

### Transitions

| Field | Type | Default | Description |
|---|---|---|---|
| `default_transition` | `str \| null` | `null` | xfade transition applied between every scene. `null` = hard cut. |
| `default_transition_duration_s` | `float` | `0.5` | Duration (seconds) for the default transition. |

Supported transition names: `fade`, `fadeblack`, `fadewhite`, `dissolve`, `pixelize`,
`wipeleft`, `wiperight`, `wipeup`, `wipedown`, `hlslice`, `hrslice`, `vuslice`, `vdslice`.

---

## Per-scene fields (`scenes[*]`)

### Transitions

| Field | Type | Default | Description |
|---|---|---|---|
| `transition` | `str \| null` | inherits `default_transition` | Override the entry transition for this scene. Scene 0 transition is always a no-op. |
| `transition_duration_s` | `float \| null` | inherits `default_transition_duration_s` | Duration (seconds) for this scene's entry transition. |

---

## Example

```yaml
aspect: "9:16"
voice: nova
image_model: mid
video_model: mid

# Dissolve 0.5s between every scene
default_transition: dissolve
default_transition_duration_s: 0.5

scenes:
  - index: 0
    vo_text: "Opening scene."
    prompt: "A sunlit kitchen."

  - index: 1
    vo_text: "Second scene."
    prompt: "A bustling market."
    # Override: wipe left instead of dissolve for this specific scene entry
    transition: wipeleft
    transition_duration_s: 0.3

  - index: 2
    vo_text: "Final scene."
    prompt: "A quiet sunset."
    # Hard cut for this scene despite the default_transition above
    transition: null
```

---

## Notes

- xfade is **video-only**. Audio (voiceover) is muxed separately and is unaffected by transitions.
- When fewer than 2 clips are assembled, xfade is skipped silently and a hard cut is used.
- `transition_duration_s` is automatically clamped to `min(clip_dur * 0.5, adjacent_clip_dur * 0.5)` — it can never exceed half the shorter of the two adjacent clips.
