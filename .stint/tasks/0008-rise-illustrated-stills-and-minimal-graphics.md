---
id: "0008"
title: "Rise Infographic: illustrated character stills and minimal vector graphics"
status: todo
priority: p1
size: m
created_at: "2026-10-16T20:35:00Z"
blocked_by: []
gh_issue: []
area:
  - "storyboard"
  - "generation"
tags:
  - "storyboard"
  - "rise"
  - "infographic"
  - "image-generation"
---

# Rise Infographic: Illustrated Character Stills & Minimal Vector Graphics

## Context & Problem Statement

In Stint 0007, Parallax landed automatic PDF export (`v0.5.50`) and established the 26-beat Rise Cortisol storyboard package. However, all 26 scene stills were code-generated using SVG card containers and pill banners. As a result, the character scenes (`scene_type: still`) currently look like programmatic Remotion slides / UI cards rather than actual full-frame 2D animation illustrated stills ready for video production.

## Objectives

### 1. Generate Proper 2D Illustrated Character Stills (10 Scenes)

Replace the programmatic SVG card containers for the 10 `scene_type: still` scenes with full-frame 9:16 2D animation illustrations matching `references/infographic-style.png` (The Infographic Show style: clean line art, stylized blonde character, soft pastel environments, expressive faces):

- **Scene 00 (Character Anchor)**:
  - Narration: *"Still tired after 8 hours of sleep?"*
  - Visual: Exhausted adult sitting up in bed with messy blonde hair, drooping sleepy eyes, alarm clock on nightstand, large graphic "8".
  - Conditioning: `parallax image generate --model mid --aspect 9:16 --ref references/infographic-style.png --out stills/scene-00-tired-after-eight-hours.png "..."`
  - Model choice: `mid` (`openai/gpt-image-2.5-flare` at ~$0.0085/image) or `nano-banana` (`google/gemini-2.5-flash-image` at ~$0.0387/image, per the reference sheet).

- **Continuity Chain (Scenes 02, 11, 13, 16, 18, 24)**:
  - Wire `image_refs` in `brief.yaml` to include BOTH `references/infographic-style.png` AND `stills/scene-00-tired-after-eight-hours.png` so the character's facial structure, hair color, and design remain consistent:
    - **Scene 02**: Bathroom mirror close-up of sad character with noticeably puffy morning cheeks, hand touching face.
    - **Scene 11**: Character sleeping uncomfortably in bed, tossing groggily with misaligned circadian clocks.
    - **Scene 13**: Busy professional at high-demanding office desk with dual monitors and coffee cups.
    - **Scene 16**: Refreshed character smiling and waking up energized on time with bright morning light.
    - **Scene 18**: Dramatic extreme close-up on character's focused, locked-in eyes with subtle target reflection.
    - **Scene 24**: Morning mirror portrait of character smiling with defined, firm, less puffy facial contour.

- **Non-Protagonist Illustrated Beats (Scenes 06, 19, 20)**:
  - **Scene 06**: 2D anatomical infographic close-up of clothed torso / waist with love handles and measurement callout.
  - **Scene 19**: Northwestern University sleep scientist in white lab coat with research clipboard in laboratory.
  - **Scene 20**: Stylized athletic figures (pro football player and Chicago Bulls basketball player in uniform).

*Total estimated image generation cost for all 10 stills on `mid`: **~$0.09**.*

### 2. Streamline Motion Graphic Stills to Minimal Graphics (16 Scenes)

Refactor `generate_infographics.py` for the 16 `scene_type: motion_graphic` scenes:
- Remove heavy slide containers: strip out outer white cards, simulated CSS drop-shadows, and top/bottom pill headers/footers.
- Make them clean, full-bleed 9:16 minimal vector graphics on soft pastel backgrounds (`#f1f5f9` / `#f8fafc`):
  - **Scenes 01, 03, 05, 07, 12**: Minimal cortisol trend lines, gauges, and ceiling spike charts.
  - **Scene 04**: Clean isometric sugar cubes beside a minimal 12:00 alarm clock.
  - **Scene 08**: Scientific 2D chemical structure drawing of the cortisol molecule ($\text{C}_{21}\text{H}_{30}\text{O}_5$).
  - **Scenes 09, 10**: Smooth 24-hour circadian sine waves and morning peak / night trough rhythm.
  - **Scenes 14, 15**: Minimal Rise sunrise logo and clean smartphone mockup of the Rise sleep window UI.
  - **Scene 17**: Daily energy map (Peak 1, Afternoon Dip, Peak 2).
  - **Scenes 21, 22, 23**: 5-day calendar streak milestone, -70% cravings bar chart, and red "X" stamped over afternoon crash.
  - **Scene 25**: Minimal typographic CTA card (*"Take the one-minute quiz and find your window"*).

### 3. Re-run Storyboard Package & Verify Outputs

- Run `parallax storyboard --folder runs/2026-10-16-storyboard-e2e/06-rise-with-refs/project --brief ...`
- Verify that `storyboard.html`, `storyboard.pdf`, and `contact-sheet.jpg` are updated.
- Verify that `handoff.json` reports `status: ready_for_review`, 0 open gates, 0 missing assets, and links both HTML and PDF.

## Done When

1. All 10 character scenes are real 2D animation illustrated stills generated with reference conditioning and scene-to-scene continuity.
2. All 16 motion graphic scenes are clean, minimal vector graphics without slide-card borders or header/footer chrome.
3. `brief.yaml` has verified scene-to-scene `image_refs`.
4. `runs/2026-10-16-storyboard-e2e/06-rise-with-refs/project/storyboard-package/` has clean `storyboard.html` and `storyboard.pdf`.
