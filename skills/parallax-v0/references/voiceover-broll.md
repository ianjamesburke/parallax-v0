# Voiceover with B-roll

The narrator tells the story while the visuals demonstrate it. Characters are participants in a scene, not presenters addressing the viewer. Use this mode when a supplied voiceover accompanies illustration, live-action B-roll, or motion graphics; choose on-screen delivery only when requested.

Give each character shot one concrete action, an object or environment to interact with, and a visual change that supports the spoken beat. Show a hand reaching for a phone, opening curtains, completing a task, sharing a meal, or stepping outside. Frame the action with varied shot sizes and purposeful viewpoints. Preserve the supplied narration wording unless the user requests a rewrite.

Reject passive front-facing character poses as filler. “Smiling at camera,” “looking confident,” and a static character-sheet stance do not establish a social-video action. Translate abstract benefits into something visible: confidence can be entering a room or beginning an activity; a fresh start can be opening a door and stepping into daylight. A reaction close-up can work when it shows a specific story change, but avoid consecutive interchangeable portraits. A camera zoom alone does not supply the missing action.

Before generation, audit the whole sequence for repeated staging, gaze toward the viewer, empty holds and disconnected symbolism. Carry the action into both the still prompt and the motion prompt, making the still a credible starting pose. During animation keep mouths naturally at rest unless the scene actually calls for speech; voiceover does not authorize lip sync or talking-head performance. Inspect the resulting footage for unintended talking.

Use one sufficient approved reference per still by default. Use a prior scene frame for a deliberate same-setting continuity link, not an automatic chain through the video. Name that relationship in the storyboard. Animate from the accepted scene still; add reference inputs only when the chosen model and a concrete continuity need justify them.

When the narration names an app or product, consider showing it rather than only a character looking at a device. Use a sourced screen inset, an over-the-shoulder view, or a split composition when it improves comprehension. Preserve official screen content and branding; label illustrative UI as a concept. Fit the demonstration into the shot's available space and duration without covering the character action or caption band.

For narration QA, compare the actual provider audio, edited narration and assembled soundtrack at any suspect word onset. A file called “raw” is not proof that no processing occurred: verify the generating command's trim and speed settings. Preserve genuine provider audio outside temporary directories. Transcription can recognize a word whose first phoneme was damaged, so exact text matching alone cannot clear audible clicks or clipping. Pause removal must retain phonetic lead-in and avoid discontinuities; inserting silence cannot recover already-deleted speech.

For narration-driven hard cuts, the incoming scene starts on its first aligned word; the outgoing shot holds through the pause. Keep captions on their absolute audio timestamps. Check the encoded frame immediately before and after representative cuts, including late in the video, to catch accumulated frame rounding. Explicit duration/start overrides and overlapping transitions can intentionally depart from word-synchronous timing; inspect those settings before adding a compensating offset. A visual timing correction should reuse the approved narration rather than retrim speech.

When pacing feedback calls for shorter sentence pauses, edit the preserved narration first. For tight targets such as 75ms, cut only measured acoustic silence within punctuation-defined sentence gaps; word-alignment boundaries alone are not safe cutting points. Preserve audible tails, breaths and leading phonemes, and distinguish the remaining quiet interval from the broader word-to-word gap. Retain the previous audio and an edit report. Verify the edited speech, replace word/caption timestamps, recompute native scene boundaries, then rerender cue-timed graphics and reassemble with existing approved visuals. Do not reuse old graphic durations or shift the video alone to compensate. Verify the new encoded cuts and final soundtrack before review.

Use the acoustic sentence command for this edit, with the approved punctuated word lock:

```bash
parallax audio cap-sentence-silence \
  --input narration-v3.wav --words narration-v3-words.json \
  --output narration-v4.wav --words-out narration-v4-words.json \
  --max-silence 0.075 --report narration-v4-edits.json
```

The cap applies to measured continuous quiet, not the complete interval between aligned words. Review retained breaths separately. The legacy `audio cap-pauses` trims estimated inter-word gaps and is not the path for a tight acoustic-silence target. Point the working plan's audio and word locks at the new outputs before recalculating scene and graphic timings.
