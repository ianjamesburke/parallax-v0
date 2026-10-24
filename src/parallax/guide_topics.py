"""Operator knowledge for `parallax guide <topic>`: plan/produce loop, locking, models, cost, motion graphics, voiceover.

Every fact here was checked against the code that enforces it (docs/skill-retirement.md lists
the sources). Authored as data so an agent reads fields, not prose walls.

"""
from __future__ import annotations

from .models import VIDEO_MODELS


def _end_frame_models() -> list[str]:
    """Read end-frame capability from the catalog; never duplicate aliases here."""
    return sorted(spec.alias for spec in VIDEO_MODELS.values() if spec.end_frame)

TOPICS: dict[str, dict] = {
    "brand": {
        "summary": "For any product or app ad, collect real brand assets before making shots.",
        "rules": ["First collect the official site, App Store lookup (`https://itunes.apple.com/lookup?id=<id>`) screenshotUrls/artworkUrl512, and an inline SVG logo where available; save each with its source URL.", "Use real app screens composited in local app-screen motion graphics—never AI-generate UI.", "Pass logo/screens as image refs only in shots that show the product, and finish on a brand CTA end card."],
        "brand_shape": {"name":"RISE","logo":"brand/logo.png","logo_on_dark":"brand/logo-white.png","app_screens":["brand/screen01.jpg"],"colors":["#721CFF"],"font":"Montserrat","cta":"Try RISE free","sources":["https://example.com"]},
        "commands": ["parallax video app-screen --screen brand/screen01.jpg --title 'Track the sleep you owe' --duration 4 --out app.mp4", "parallax video end-card --logo brand/logo-white.png --headline 'Become a morning person' --cta 'Try RISE free' --duration 3 --out end.mp4"],
        "next": "assemble",
    },
    "motion_graphic": {
        "summary": "Zero-spend, safe-area-aware local motion graphics. Use real product screens and brand colors.",
        "commands": ["parallax video app-screen --screen shot.jpg --callout '6.3 HRS SLEEP DEBT' --highlight .1,.2,.5,.2 --duration 4 --out app.mp4", "parallax video diagram --preset clock-window --duration 3 --out clock.mp4", "parallax video end-card --logo logo.png --duration 3 --out end.mp4", "parallax video text-overlay --bg-color '#14102A' --text 'SLEEP DEBT' --duration 2 --out type.mp4"],
        "brand_note": "For product shots use actual screenshots, never generated UI; complete the sequence with a branded CTA end card.", "next":"assemble",
    },
    "mock": {"summary":"Iterate structure, timing, and captions in PARALLAX_TEST_MODE=1, then rerun the same commands for real.", "next":"produce_loop"},
    "voiceover": {"summary":"Seed Audio requires a natural-language voice_prompt. Social default: A fast documentary narrator revealing a secret: fast-paced social-media-ad delivery, rapid with minimal pauses between lines, highly expressive animated storyteller building intrigue, deep, low, warm, slightly gravelly male voice, punchy emphasis on key words.", "next":"assemble"},
    "single_shot": {
        "summary": "One camera move, using separate frozen start and end frames when the view, scale, or subject changes.",
        "separate_stills": {
            "when": "Make separate start and end stills for any viewpoint, scale, or subject change: overhead to pillow-level macro, pushing into an object, or a reveal.",
            "commands": [
                "parallax image generate \"<one frozen start moment>\" --aspect 9:16 --out START.png",
                "parallax image generate \"<one frozen end moment>\" --aspect 9:16 --out END.png",
            ],
            "continuity": "Keep style and palette consistent. Use a character sheet as --ref only in stills where that character appears; drop a reference that carries the wrong material (for example papercraft when the shot needs a realistic human).",
        },
        "still_prompt_rule": "NEVER put a multi-step camera move in a still prompt: image models turn it into a comic/panel grid. Describe ONE frozen moment; put motion in the video prompt.",
        "animate": {
            "command": "parallax video animate --start START.png --end END.png --model <tier> --duration N --prompt \"<move>\" --out shot.mp4",
            "end_frame_models": _end_frame_models(),
            "default_note": "The default video model may not support end frames. With --end, Parallax fails before spending and names the supported models above.",
        },
        "video_prompt_layout": [
            "Named move.", "Start state.", "Continuous motion.", "Settled end state.", "Tone, lighting, texture."
        ],
        "clip_scope": "One move per clip. Give a complex move more duration or split it into two clips.",
        "audio": "Model-generated clip audio is stripped before assembly; do not depend on it.",
        "wait": "animate can block for minutes with no output. Do not kill or retry a paid call; rerun the same command to resume/reuse saved work.",
        "privacy_rejection": "If Seedance rejects an input image as a possible real person, hide faces or stylize it, or explicitly choose another model such as `--model kling` (pricier).",
        "next": "asset_locking",
    },
    "assemble": {
        "summary": "Zero-spend local finishing: concatenate clips/stills, voiceover, and safe captions without raw ffmpeg.",
        "commands": [
            "parallax video ken-burns --image still.png --duration 3 --move in --out clip.mp4",
            "parallax video text-overlay --in still.png --text '+20%' --preset count-up --y .50 --size 96 --color '#ffffff' --resolution 1080x1920 --duration 3 --out graphic.mp4",
            "parallax video assemble --shots shots.json --voice vo.wav --voice-offset 1 --words words.json --captions --caption-y .72 --report caption-boxes.json --resolution 1080x1920 --out final.mp4",
            "# --shots shots.json also accepts clip {\"path\": \"clip.mp4\", \"trim_start\": 1.5, \"duration\": 2.0, \"speed\": 1.25}",
            "parallax video captions --video final.mp4 --words words.json --report boxes.json --out captioned.mp4",
        ],
        "contract": "All commands use local ffmpeg only. text-overlay outputs a silent clip appropriate for scene_type: motion_graphic clip_path. Caption reports expose actual safe-area boxes.",
        "social_ad_example": "9:16 social ad: shots.json starts with animated clips using trim_start and speed; after about 15 seconds use stills with move: pan-left/pan-right; insert the count-up graphic as a motion_graphic clip; then assemble at 1080x1920 with the delayed VO, --caption-y .72, and --report.",
    },
    "character_first": {
        "summary": "For a new character-driven ad, lock an approved character sheet before storyboarding or generating production shots.",
        "steps": [
            {"step": 1, "do": "Get the user's character and style description."},
            {"step": 2, "do": "Generate one 2x4 or 4x2 grid with eight clearly numbered, different character options.", "template": "An 8-panel 2x4 character design grid for <CHARACTER DESCRIPTION>, eight distinct <STYLE> designs. Print a clear number 1–8 inside each panel; no extra panels."},
            {"step": 3, "do": "User picks one number; do not choose for them."},
            {"step": 4, "do": "Crop the selection, then use it as --ref for a sheet.", "crop": "parallax image crop-grid options.png --rows 2 --cols 4 --pick N --out selected.png", "sheet_template": "Character sheet for the selected character: front, 3/4, side, back, a few action poses, scale reference, and color palette. Preserve the selected design, materials, and style."},
            {"step": 5, "do": "Lock the approved sheet as brief character_image, then storyboard (plan --stills-only / produce)."},
        ],
        "asset_locking": "Do not regenerate approved grid picks or character sheets. Keep and lock the approved assets; use the sheet as the character reference for applicable shots.",
        "next": "produce_loop",
    },
    "produce_loop": {
        "summary": "brief.yaml (human spec) -> `parallax plan` -> plan.yaml (engine spec) -> `parallax produce` -> <folder>/parallax/output/vN/.",
        "paths": {
            "folder": "--folder is the project root. Pass an absolute path; paths inside plan.yaml are relative to it.",
            "plan_from_brief": "<folder>/parallax/scratch/plan.yaml (override with `plan --out`). When assets are missing, `plan` writes <folder>/parallax/scratch/questions.yaml instead and exits 1.",
            "generated_assets": "<folder>/parallax/assets/scene_NN_still.png and scene_NN_animated.mp4 (reused by later runs; regenerated takes archive the prior version into <folder>/parallax/assets/versions/scene_NN_still_vK.png).",
            "run_output": "<folder>/parallax/output/vN/ (N increments each run): <project>-vN-<hash>.mp4, plan.yaml (snapshot of the plan that ran; do not edit), manifest.yaml, cost.json, run.log, audio/.",
        },
        "flow": [
            {"step": "validate", "command": "parallax validate --folder F --brief B", "spends": False, "use": "Catches brief and plan errors before any spend; prints JSON with `errors` and `warnings`."},
            {"step": "plan", "command": "parallax plan --folder F --brief B", "spends": False, "use": "Writes plan.yaml. Inspect and edit it before producing (model picks, locks, caption settings)."},
            {"step": "dry_run", "command": "PARALLAX_TEST_MODE=1 parallax produce --folder F --plan P", "spends": False, "use": "Full pipeline on stub assets with no network; spends nothing."},
            {"step": "produce", "command": "parallax produce --folder F --plan P", "spends": True, "use": "Real run. Prints a pre-flight cost estimate first, then proceeds. Re-running regenerates only unlocked scenes."},
        ],
        "one_shot": "`parallax produce --folder F --brief B` plans from the brief then produces in one command. Use the two-step form when the plan needs inspection or edits first.",
        "produce_flags": {
            "--scene N": "Render only scene N, not the full pipeline.",
            "--hq": "Use premium-tier models.",
            "--resolution WxH": "Writes `resolution` into the plan being run (also on `plan`).",
            "--stills-only": "Generate stills only (writes `stills_only: true` into the plan; also on `plan`); skips voiceover, animation, and assembly.",
            "--debug 1|2|3": "Burn a debug overlay on every scene (1 index, 2 +prompt, 3 +script and motion).",
        },
        "plan_settings": "Model, caption, headline, and transition settings live in plan.yaml, not brief.yaml. `parallax schema plan` lists every field with its default; unknown or renamed fields are rejected at load.",
        "prerequisites": {
            "ffmpeg": "`ffmpeg` and `ffprobe` must be on PATH for every produce run, including PARALLAX_TEST_MODE (produce fails fast otherwise). macOS: `brew install ffmpeg-full`; Debian/Ubuntu: `sudo apt install ffmpeg`. Captions and headlines need the drawtext filter; without it they fall back to a slower path.",
            "api_key": "OPENROUTER_API_KEY for real (paid) runs; test mode needs none.",
        },
        "dry_run": "PARALLAX_TEST_MODE=1 runs every stage on stub assets and writes nothing back into plan.yaml (no still_path, clip_path, audio locks), so it is safe on the real plan and the real cost estimate still counts those scenes as unlocked. Stub files land under <folder>/parallax/ and are overwritten, not reused, by a real run.",
        "resolution": "Output resolution comes from plan `resolution` (or `--resolution`). When unset it is the largest resolution among the scenes' existing clip_path files, else the aspect default (9:16 is 720x1280, 16:9 1920x1080, 1:1 1080x1080). Locking or adding a clip can therefore change it between runs: set `resolution` explicitly to pin it. Animation requests use a cheaper `animate_resolution` (9:16 default 480x854) that assembly scales up.",
        "stills_only": "Plan field `stills_only: true` (or `--stills-only` on `plan` and `produce`) runs only the stills stage: no audio, animation, or assembly. Use it to generate and approve stills first, including before `motion_graphic` clips are rendered.",
        "review_checkpoints": [
            "Storyboard: `parallax storyboard --json`, answer open gates, show storyboard-package/storyboard.html, collect feedback on scene breakdown and treatments.",
            "Stills: after storyboard approval, generate stills (stills_only plan), inspect them against the scene/reference map, lock the approved ones.",
            "Narrated draft and motion: with approved stills, produce a draft with captions skipped (`captions: skip`), check narration against vo_text, then a small animation pilot before extending to every shot.",
            "Final: inspect captions and every scene transition in the assembled video, decode the export. Mechanical checks are not human acceptance.",
        ],
        "checkpoints_note": "Conversational checkpoints, not an enforced CLI state machine. A simulated user response cannot authorize spend or advance a human gate.",
        "after_each_artifact": "Show the artifact to the human (open the image, audio, video, or storyboard.html) before declaring the stage done. A successful command is not evidence of style, character, or product fidelity.",
        "next": "asset_locking",
    },
    "asset_locking": {
        "summary": "Approved assets are locked in plan.yaml so a re-run regenerates only unlocked scenes. Unlocked scenes cost money.",
        "automatic": "`produce` writes each generated still (`still_path`) and animated clip (`clip_path`) back into the working plan.yaml (and `source_brief` brief.yaml when present, refreshing `storyboard-package/`) as it goes, so an interrupted or repeated run reuses them. It patches only that scene's field and keeps your other edits.",
        "scene_locks": {
            "still_path": "Reuse this image instead of generating one.",
            "clip_path": "Reuse this clip instead of animating. Required for scene_type: motion_graphic.",
            "end_frame_path": "Cleared together with the other scene locks by `regenerate`.",
            "reference": "true: use plan.character_image as the still reference.",
            "reference_images": "Explicit per-scene references; merged with applicable global references.",
        },
        "regenerate": "Set `regenerate: true` on a scene (in `brief.yaml` or `plan.yaml`): the next `produce` clears its still_path, clip_path, and end_frame_path locks and the flag itself, moves the previous still/clip into `<folder>/parallax/assets/versions/scene_NN_still_vK.png` (so prior takes are cleanly preserved), and generates a fresh take at `<folder>/parallax/assets/scene_NN_still.png`. Top-level audio locks are not cleared.",
        "audio_locks": {
            "where": "Top level of plan.yaml, not inside a scene: `audio_path` and `words_path`. `produce` writes them itself only when `voice_postprocess` runs; otherwise set them by hand after approving narration.",
            "effect": "While set, narration is reused, not synthesized. `voice_speed`, `trim_pauses`, and `voice_postprocess` are ignored (the pre-flight table warns).",
            "drift": "`vo_text_hashes` records each scene's vo_text when audio is first locked. Editing a scene's vo_text afterwards triggers a pre-flight warning; clear audio_path to regenerate.",
            "preparing": "Copy approved audio and word-timing files to run-local paths first, keep scene narration matched to the recording, and keep the reviewed voice speed and post-processing settings.",
        },
        "paths": "Locked paths resolve from the project folder (--folder).",
        "broll_reuse": "When creating a new concept variation or when the project folder supplies a reusable asset pool (e.g. `assets/reused/` or `reuse-index.json`), match reusable stills and clips by story state (problem vs. healthy/after, setting, character/product continuity), lock them on matching scenes in `brief.yaml` via `still_path` / `clip_path`, and generate only the newly needed coverage (e.g. new opening hook shots).",
        "rules": [
            "Never edit <folder>/parallax/output/vN/plan.yaml: it is a snapshot of the run. Edit the working plan.",
            "Lock approved assets before re-running.",
            "A brief scene can also carry still_path and clip_path; `parallax plan` carries them into plan.yaml.",
        ],
        "next": "models",
    },
    "models": {
        "summary": "Choose models from the installed catalog, never from memory: names, prices, and limits change.",
        "commands": {
            "parallax models list": "All aliases with price, aspect support, reference limits, and native resolution. `--kind image|video|tts` filters.",
            "parallax models show <alias>": "Full spec for one alias: model_id, cost, fallback, supported aspects, reference limit, inputs.",
        },
        "plan_fields": {
            "image_model": "Alias for stills (default mid).",
            "video_model": "Alias for animation (default draft).",
            "voice_model": "Alias for narration (default tts-mini).",
            "per_scene": "A scene may override image_model, video_model, voice_model.",
        },
        "tiers": "`draft` / `mid` / `premium` exist per kind; `--hq` on produce selects the premium tier. Named aliases (e.g. grok-image, veo-lite, tts-gemini) come from the catalog.",
        "choose_by": ["the approved references (the model's max reference count and supported inputs)", "output aspect and resolution", "shot duration", "the agreed budget"],
        "tts": "`tts-gemini` uses the dedicated `/audio/speech` endpoint (voices: Kore, Fenrir, Aoede, Sulafat, etc.), reads long multi-scene scripts verbatim without chat-model abridgement, and passes inline [emotional] tags (e.g. `[dramatically] Hello.`) to the model. `tts-mini` (OpenAI chat_audio, voices: nova, alloy, shimmer, etc.) strips tags and is best for short scripts; prefer `voice_model: tts-gemini` with a matching Gemini voice (`Kore`, `Aoede`, etc.) for multi-scene scripts (10+ scenes).",
        "style_fields": "Plan `style` and `style_hint` steer voice synthesis only, not still generation. Put the art style in each executable still prompt.",
        "next": "cost",
    },
    "cost": {
        "summary": "Paid commands print their estimated cost first and then proceed. There is no approval step: watch the printed estimate. The OpenRouter key limit is your own backstop. Catalog prices are estimates.",
        "estimate_before_spending": [
            "`parallax models list` for per-image, per-second, and per-1k-character prices.",
            "`parallax produce` prints a pre-flight cost table before paid work; locked scenes cost nothing. `image generate`, `image analyze`, `video animate`, and `audio voiceover` print `Estimated cost: ...` to stderr before the provider call.",
            "`parallax ingest <path> --estimate` reports clip duration and transcription cost without transcribing.",
        ],
        "track_actuals": {
            "parallax usage": "Recorded generation spend by model and session.",
            "parallax credits": "OpenRouter credit balance.",
            "parallax log latest | parallax log list": "Run summary / all runs. <folder>/parallax/output/vN/cost.json holds each run's cost.",
        },
        "budget_rule": "Count billed calls, including failed and retried ones, against the agreed cap. Estimates are not receipts.",
        "approval": "There is no approval step, budget file, or `--yes`. Show the user the printed estimate; they can stop the run.",
        "dry_run": "PARALLAX_TEST_MODE=1 stubs every provider call: no network, no spend, and nothing is written into plan.yaml.",
        "next": "produce",
    },
    "motion_graphic": {
        "summary": "scene_type: motion_graphic is a locally rendered clip. Parallax never AI-generates it; render it with the zero-spend `parallax video` primitives below (or your own tool) and set clip_path.",
        "local_primitives": {
            "app_screen": "`parallax video app-screen --screen shot.png --full-bleed --highlight x,y,w,h --callout TEXT [--callout-y 0.6] --duration S --out c.mp4`. Titles and any opening text are fully visible on frame 0 by default; `--title-fade-in S` is an explicit override. --full-bleed for App Store screenshots that already include a device frame + headline (fills 9:16, push-in, glowing highlight, pop callout; pixels as-is). Without it the screenshot sits in a drawn phone frame. Crop marketing text off the bottom first if captions would sit on it.",
            "diagram": "`parallax video diagram --preset clock-window --label .. --sub ..` | `--preset meter --value 6.3 --max 10 --unit \" HRS\" --label ..` | `--preset curve --label .. --peak PEAK --dip DIP`. Brand colors via --colors hex,hex,...",
            "end_card": "`parallax video end-card --logo wordmark.png --icon icon.png --headline .. --cta .. --sub .. --duration 3`: icon pop, wordmark, headline slide-up, pulsing CTA pill.",
            "kinetic_text": "`parallax video text-overlay --bg-color '#14102A' --text .. --preset pop|count-up|slide-up|typewriter --duration S --out c.mp4` (or --in still/clip).",
            "layout": "Graphics stay above y=0.70 of the frame, so captions at `--caption-y 0.78` never collide. For 1080x1920 social ads use `--caption-style clean --caption-size 78 --words-per-chunk 3` (55 px single words read small).",
        },
        "contract": {
            "brief_scene": "scene_type: motion_graphic plus clip_path (a rendered MP4 under the project folder). Optional still_path for the poster. `prompt` and `motion_prompt` describe the graphic for review.",
            "storyboard": "A scene with no existing clip file is storyboarded with advisory `motion_graphic_clip_missing`, and `produce` stays blocked (`blocked_by`) until the clip exists.",
            "plan_and_produce": "`parallax plan` (without `--stills-only`) and full `plan.yaml` loading reject a motion_graphic scene without clip_path; `produce` fails fast if the clip file is missing. `--stills-only` allows planning and generating non-graphic stills before motion_graphic clips exist. It never falls through to paid image or video generation.",
            "reference_gate": "motion_graphic scenes are excluded from the editorial reference gate.",
            "animate": "scene_type implies `animate`; setting it inconsistently (a motion_graphic with animate: true) is a validation error.",
        },
        "example_scene": {
            "index": 3, "shot_type": "screen", "scene_type": "motion_graphic",
            "vo_text": "On day 14, I finally felt like I had control.",
            "prompt": "The approved illustrated timeline and day-14 milestone.",
            "motion_prompt": "The marker reaches day 14 on the spoken cue.",
            "still_path": "graphics/day-14/poster.png", "clip_path": "graphics/day-14/final.mp4",
        },
        "clip_convention": {
            "clip": "Any MP4 under the project folder, set as the scene's `clip_path`. Assembly normalizes it to the output resolution at 30 fps and loops or trims it to the scene's duration, so render at the output dimensions, 30 fps, with the measured scene length to avoid looping.",
            "poster": "Optional still (PNG) set as `still_path` for layout review; not used for assembly.",
            "location": "Conventionally graphics/<name>/final.mp4 and graphics/<name>/poster.png, kept with the source composition.",
            "placeholder": "To test the pipeline before the real graphic exists (no drawtext filter needed): `ffmpeg -f lavfi -i testsrc2=size=1080x1920:rate=30:duration=2 -c:v libx264 -pix_fmt yuv420p -an graphics/<name>/final.mp4` and `ffmpeg -f lavfi -i testsrc2=size=1080x1920 -frames:v 1 graphics/<name>/poster.png`. Replace it with the approved render before a real run.",
        },
        "output_contract": [
            "Poster image for layout review.",
            "Silent MP4 at the project's output dimensions and frame rate, with the measured scene duration.",
            "Source composition and cue data kept in the project (conventionally graphics/) so a narration revision can be re-rendered.",
            "Playback and sampled frames at native resolution: first frame, the first few tenths of a second, each cue, the end.",
        ],
        "workflow": [
            "Before rendering, fix the narration, exact on-screen text, visual hierarchy, aspect, resolution, and frame rate.",
            "When generating or authoring `scene_type: motion_graphic` layout posters (`still_path`) or animated clips (`clip_path`), use the Remotion skill (`remotion-best-practices` / `create-remotion-geist`) if available in the agent environment, adapting the visual style to the project's supplied references rather than a default template theme.",
            "Review a layout poster first. A silent timing sketch may use estimated duration; label it a preview.",
            "After narration approval, render with measured scene boundaries (`parallax storyboard --timing <folder>/parallax/output/vN/manifest.yaml`, which carries index, start_s, end_s per scene) and scene-relative cue times; keep estimated durations out of duration_s locks.",
            "Set clip_path in the brief, run `parallax storyboard` again, then plan and validate.",
            "If narration or scene boundaries change, re-render cue-driven graphics and keep the approved audio unchanged for a visual timing revision.",
        ],
        "design_rules": [
            "Use a motion graphic for an exact diagram, timeline, interface schematic, comparison, or typography beat; not as a fallback for every line of narration (advisories motion_graphic_share above 40% of scenes, motion_graphic_run for 3+ consecutive).",
            "Label a schematic app screen as a concept, not a product screenshot.",
            "Start motion at the beginning of the shot: give elements an early entrance instead of a static card until a spoken cue; keep the meaningful change synchronized to the narration.",
            "For vertical 9:16, keep essential labels clear of the bottom third and top platform chrome; reserve the caption band before composing, and check the largest zoom state with captions overlaid.",
            "Use bounded text layout and inspect rendered frames; fix the template, not one overflowing card.",
            "Do not independently infer a second timing map: reuse the assembler's scene boundaries and word cues.",
        ],
        "renderer": "Use the Remotion skill (`remotion-best-practices` / `create-remotion-geist`) if available in the agent environment when generating or authoring `scene_type: motion_graphic` layout posters (`still_path`) or animated clips (`clip_path`), adapting the visual style to the project's supplied references rather than a default template theme. Any local renderer that satisfies the output contract works; renderer choice does not dictate aesthetic. If a repeated operation needs engine access, report the missing CLI operation instead of adding a project-specific helper.",
        "next": "voiceover_broll",
    },
    "voiceover_broll": {
        "summary": "Voiceover-led social video: the narrator tells the story, visuals demonstrate it. Characters take part in scenes; they do not present to the viewer.",
        "mode": "A first-person voiceover does not imply a talking avatar. Choose on-screen presenter delivery only when requested.",
        "scene_rules": [
            "One readable visual beat per scene: one concrete action, an object or environment to interact with, a visual change that supports the spoken beat.",
            "Reject passive front-facing poses as filler ('smiling at camera', 'looking confident'). Translate abstract benefits into visible actions.",
            "Vary shot size and viewpoint; avoid consecutive interchangeable portraits. A camera zoom alone does not supply an action.",
            "Carry the action into both the still prompt and motion_prompt; the still must be a credible starting pose.",
            "Keep mouths at rest during animation unless the scene calls for speech; voiceover does not authorize lip sync. Inspect footage for unintended talking.",
            "Use one sufficient reference per still by default. Use a prior scene frame only for a deliberate same-setting continuity link, and name it in the storyboard.",
            "Animate from the accepted scene still; add reference inputs only when the model and a concrete continuity need justify them.",
            "When narration names an app or product, consider showing it (screen inset, over-the-shoulder, split composition). Preserve official screen content; label illustrative UI as a concept.",
            "Do not send a compound direction (diptych, split screen, before/after, 'does X then Y') to one still: it raises advisory `compound_direction`. Split it into beats or make it an approved motion_graphic.",
            "Preserve the supplied narration wording unless the user asks for a rewrite.",
        ],
        "opening_review": {
            "advisories": "Opening pacing advisories (kinds opening_long_hold, opening_few_cuts, opening_delayed_first_word) appear in `storyboard --json` `advisories` and `validate` warnings. A shot over 3s, or fewer than 3 cuts in the first 6s, needs a deliberate exception or a revised breakdown.",
            "after_narration": "`parallax storyboard --timing <aligned manifest>` replaces reading estimates with measured scene lengths for the pacing advisories.",
            "judgement": "More cuts alone do not prove excitement. Compare the first twelve seconds in storyboard.html; split narration at meaningful words into distinct actions, preserving the script.",
        },
        "narration_commands": {
            "parallax audio trim-leading-quiet": "Remove proven opening quiet without touching the first spoken phoneme (target first word within 0.1s). If it cannot reach the target safely, get a better take instead of clipping speech.",
            "parallax audio cap-sentence-silence": "Shorten measured quiet between sentences using punctuation-defined boundaries and acoustic silence (default cap 0.075s); writes revised word timestamps (--words-out) and an edit report. Preferred for tight pause targets.",
            "parallax audio cap-pauses": "Older word-timestamp gap trimming. Not for tight acoustic-silence targets.",
            "parallax audio speed": "Change tempo (--rate or --by) on an existing file.",
            "parallax audio transcribe | detect-silences | trim | pad-onsets": "Inspect and edit audio; read each command's --help.",
        },
        "narration_edit_chain": "Edit preserved narration first, keep the previous audio and the edit report, verify the edited speech, then update the plan's audio_path and words_path locks, recompute scene boundaries, re-render cue-timed graphics, and reassemble with approved visuals. Never shift the video alone to compensate for audio edits.",
        "narration_qa": [
            "Compare provider audio, edited narration, and assembled soundtrack at any suspect word onset. A file named 'raw' is not proof nothing was processed: check the generating command's trim and speed settings.",
            "Transcription can recognize a word whose first phoneme was damaged; exact text matching does not clear audible clicks or clipping.",
            "Pause removal must keep phonetic lead-in; inserted silence cannot recover deleted speech.",
        ],
        "cuts": [
            "Narration-driven hard cuts: the incoming scene starts on its first aligned word; the outgoing shot holds through the pause.",
            "Captions keep absolute audio timestamps.",
            "Check the encoded frame before and after representative cuts, including late in the video, to catch accumulated frame rounding.",
            "Explicit duration_s / start_offset_s and overlapping transitions intentionally depart from word-synchronous timing; inspect them before adding a compensating offset.",
        ],
        "captions": {
            "fields": "Plan fields caption_style, fontsize, words_per_chunk ('smart' or an integer), caption_shift_s, captions ('skip' disables).",
            "words_per_chunk": "Both modes stop at sentence or phrase punctuation. An integer N makes groups of at most N words; 'smart' keeps mostly one-word groups, so use an integer for short phrases.",
            "portrait_start": "caption_style {base: anton, y_expr: \"h*64/100-th\"}, fontsize 44, words_per_chunk 3. y_expr places the text bottom at 64% of frame height; fontsize is the 1080px-wide baseline and scales with output width.",
            "review": "Preserve canonical script punctuation when matching words to timestamps; stop and inspect token mismatches instead of replacing unmatched text. Keep caption boxes clear of faces, UI, and key graphics; a successful burn does not prove readability.",
        },
        "next": None,
    },
}
