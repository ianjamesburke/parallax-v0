# Motion graphics in a Parallax project

Use a motion graphic for an exact diagram, timeline, interface schematic, comparison or typography beat. Describe it in the storyboard as `scene_type: motion_graphic`. Keep the visual design consistent with the supplied reference. Renderer choice does not dictate an aesthetic.

Parallax accepts a rendered local clip; it does not yet generate Remotion compositions or map narration words to graphics automatically. Keep the reusable workflow here and authored compositions in `graphics/` in the production project. Avoid adding project-specific Python utilities that reach into Parallax internals. If a repeated operation needs engine access, report the missing CLI operation rather than describing a local helper as a shipped feature.

## Author and review

Before rendering, establish the scene's narration, exact on-screen text, visual hierarchy, aspect, resolution and frame rate. Label any schematic app screen as a concept, not a screenshot of the product. Use the project's illustration and type direction; do not inherit an unrelated renderer template's branding.

Create a layout poster for still review. A silent timing sketch may use estimated duration, but label it as a preview. After narration approval, use measured scene boundaries and scene-relative cue times. Keep estimated durations out of production `duration_s` locks. Retain the source composition, cue data and rendered output together so a narration revision can be rerendered.

For Remotion authoring, use the available Remotion guidance when installed. For a simpler graphic, choose an appropriate local renderer. The output contract is the same:

- Poster image for layout review.
- Silent MP4 at the project's output dimensions and frame rate, with the measured scene duration.
- Source composition and timing data for revision.
- Native-resolution playback and sampled frames showing the start, each cue, and the end.

Inspect text size, safe areas, reference fidelity, cue timing and motion during playback. A contact sheet alone cannot prove motion quality. Obtain approval before treating the clip as final.

## Integrate

After rendering, set the local asset paths in the brief:

```yaml
- index: 3
  shot_type: screen
  scene_type: motion_graphic
  vo_text: "On day 14, I finally felt like I had control."
  prompt: "The approved illustrated timeline and day-14 milestone."
  motion_prompt: "The marker reaches day 14 on the spoken cue."
  still_path: graphics/day-14/poster.png
  clip_path: graphics/day-14/final.mp4
```

Refresh `parallax storyboard`, then materialize and validate the production plan. A missing motion-graphic clip blocks production instead of falling through to paid image or video generation. For a stills-only batch before graphics exist, include only the image scenes in its execution plan; preserve the complete authored storyboard.

Reassemble using the approved audio and word locks. Verify the encoded clip boundaries against the narration and watch the resulting full edit. If the narration changes, review the cue times and render a new version before replacing the lock.
