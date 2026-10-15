# Motion graphics in a Parallax project

Use a motion graphic for an exact diagram, timeline, interface schematic, comparison or typography beat. Describe it in the storyboard as `scene_type: motion_graphic`. Keep the visual design consistent with the supplied reference. Renderer choice does not dictate an aesthetic.

Parallax accepts a rendered local clip; it does not yet generate Remotion compositions or map narration words to graphics automatically. Keep the reusable workflow here and authored compositions in `graphics/` in the production project. Avoid adding project-specific Python utilities that reach into Parallax internals. If a repeated operation needs engine access, report the missing CLI operation rather than describing a local helper as a shipped feature.

## Author and review

Before rendering, establish the scene's narration, exact on-screen text, visual hierarchy, aspect, resolution and frame rate. Label any schematic app screen as a concept, not a screenshot of the product. Use the project's illustration and type direction; do not inherit an unrelated renderer template's branding.

Create a layout poster for still review. A silent timing sketch may use estimated duration, but label it as a preview. After narration approval, use measured scene boundaries and scene-relative cue times. Keep estimated durations out of production `duration_s` locks. Retain the source composition, cue data and rendered output together so a narration revision can be rerendered.

State how each preview was made: a code-rendered layout poster is a static image of editable shapes, not proof of animation. Keep important layout relationships in shared coordinates: a person inside a ring should use the ring's center and the person's visible bounds, rather than unrelated hard-coded offsets. Check optical balance as well as geometry at native size, including start, intermediate and final animation states. Preserve intentional asymmetry when it supports the composition.

Design motion from the beginning of the shot. When a diagram change waits for a spoken cue, give its elements an early entrance (such as a short fade and settle) rather than holding a static card until that word. Keep the meaningful change synchronized to narration. Treat element animation and camera movement as separate choices. For social B-roll infographics, propose a continuous composition-level push or drift in addition to counters, sliders and path reveals. A small scale change on one diagram alone may be imperceptible and does not move the scene. Judge the camera move at playback size across the full shot; keep labels readable and check the largest scale against safe areas. CTA arrows should begin visibly animating near the scene entrance, not wait until the spoken CTA is nearly over. Sample the first frame, the first few tenths of a second, each cue, and the ending.

Reserve a common caption band before composing the graphics. For vertical social output, keep essential labels, branding and controls clear of the bottom third and top platform chrome; use the project's explicit safe-area guide rather than assuming empty lower space is safe. Check the complete video with captions overlaid, including the largest zoom state. Remove headings that merely repeat labels already in the graphic. Reuse the assembler's actual scene boundaries and word cues; do not independently infer a second timing map. For word-synchronous cuts, hold the outgoing shot through the inter-phrase pause and start the incoming shot at its first spoken word. Verify encoded cuts within one output frame of the word timestamps: contiguous scenes alone do not prove synchronization, and assigning the pause to the incoming scene makes every cut feel early. Rerender cue-driven graphics whenever scene boundaries change; keep the approved audio unchanged for a visual timing revision.

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
