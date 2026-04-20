"""
One-shot Fitbod ad pipeline runner.
Reuses pre-generated stills in output/ and drives:
  generate_voiceover → align_scenes → ken_burns_assemble → burn_captions → burn_headline
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from parallax.tools_video import (
    generate_voiceover,
    align_scenes,
    ken_burns_assemble,
    burn_captions,
    burn_headline,
)

BASE = Path(__file__).parent / "output"
OUT = str(BASE)

# ---------------------------------------------------------------------------
# Scene definitions: vo_text must exactly match what ElevenLabs will say
# (word count drives alignment). Full text = concatenation of all vo_text.
# ---------------------------------------------------------------------------
scenes = [
    {
        "index": 1,
        "still_path": str(BASE / "mid_10794912fa.jpg"),
        "vo_text": "Here's what would happen if a man stopped fucking around and locked in with Fitbod for 5 weeks straight.",
    },
    {
        "index": 2,
        "still_path": str(BASE / "mid_cd92ecc0a1.jpg"),
        "vo_text": "Week 1: He stops winging it.",
    },
    {
        "index": 3,
        "still_path": str(BASE / "mid_39ce9d285b.jpg"),
        "vo_text": "No more showing up with zero plan or doing the same three exercises he learned in high school PE.",
    },
    {
        "index": 4,
        "still_path": str(BASE / "mid_2cc90a802c.jpg"),
        "vo_text": "Fitbod builds a surgical program based on his goals. The gym finally makes sense for the first time in his life.",
    },
    {
        "index": 5,
        "still_path": str(BASE / "mid_9893839ac0.jpg"),
        "vo_text": "Week 2: He's sore in places he didn't know he had.",
    },
    {
        "index": 6,
        "still_path": str(BASE / "mid_bccce5540f.jpg"),
        "vo_text": "Not injured. Just I've been skipping these muscles for two years sore.",
    },
    {
        "index": 7,
        "still_path": str(BASE / "mid_b35990d0a1.jpg"),
        "vo_text": "Every move has a step-by-step demo so he finally stops bicep curling with his entire lumbar spine.",
    },
    {
        "index": 8,
        "still_path": str(BASE / "mid_001f7f1b93.jpg"),
        "vo_text": "Week 3: Something shifts.",
    },
    {
        "index": 9,
        "still_path": str(BASE / "mid_fb3da70de6.jpg"),
        "vo_text": "He's adding weight every session.",
    },
    {
        "index": 10,
        "still_path": str(BASE / "mid_cf2c77af07.jpg"),
        "vo_text": "His form doesn't look like a crime scene anymore.",
    },
    {
        "index": 11,
        "still_path": str(BASE / "mid_300d3e9b8e.jpg"),
        "vo_text": "He's walking into the gym with a mission instead of just aggressive eye contact with the squat rack.",
    },
    {
        "index": 12,
        "still_path": str(BASE / "mid_49a604b588.jpg"),
        "vo_text": "Week 4: He starts noticing the shift.",
    },
    {
        "index": 13,
        "still_path": str(BASE / "mid_1950c9a94e.jpg"),
        "vo_text": "Clothes fitting different.",
    },
    {
        "index": 14,
        "still_path": str(BASE / "mid_b3fbbd8aa3.jpg"),
        "vo_text": "Feeling stronger.",
    },
    {
        "index": 15,
        "still_path": str(BASE / "mid_0a26f94c88.jpg"),
        "vo_text": "Realizing he's been leaving gains on the table for years and feeling personally attacked by that information.",
    },
    {
        "index": 16,
        "still_path": str(BASE / "mid_fe170aef6c.jpg"),
        "vo_text": "Week 5: He's locked in.",
    },
    {
        "index": 17,
        "still_path": str(BASE / "mid_c24fb09d19.jpg"),
        "vo_text": "Fitbod keeps progressively overloading his workouts so he never plateaus, never gets bored, and now it's his turn to turn down his ex.",
    },
    {
        "index": 18,
        "still_path": str(BASE / "mid_4676753b4f.jpg"),
        "vo_text": "Over 15 million downloads and 120 million plus workouts logged with Fitbod.",
    },
    {
        "index": 19,
        "still_path": str(BASE / "mid_9e916a584e.jpg"),
        "vo_text": "Stop fucking around. Get Fitbod today.",
    },
]

full_text = " ".join(s["vo_text"] for s in scenes)

print(f"Script: {len(full_text.split())} words across {len(scenes)} scenes")

# Step 1: Voiceover
print("\n[1/5] Generating voiceover (george, 1.1x)...")
vo = json.loads(generate_voiceover(full_text, voice="george", speed=1.1, out_dir=OUT))
print(f"      audio:    {vo['audio_path']}")
print(f"      duration: {vo['total_duration_s']:.1f}s")

# Step 2: Align scenes to word timestamps
print("\n[2/5] Aligning scenes...")
aligned_json = align_scenes(json.dumps(scenes), vo["words_path"])
aligned = json.loads(aligned_json)
for s in aligned:
    start = s.get("start_s", 0)
    end = s.get("end_s", 0)
    print(f"      {s['index']:02d}  {start:5.1f}s – {end:5.1f}s")

# Step 3: Ken Burns assembly
print("\n[3/5] Assembling Ken Burns draft (1080x1920)...")
draft = ken_burns_assemble(
    aligned_json,
    vo["audio_path"],
    output_path=str(BASE / "fitbod_draft.mp4"),
    resolution="1080x1920",
)
print(f"      draft: {draft}")

# Step 4: Captions
print("\n[4/5] Burning captions (anton, 55px)...")
captioned = burn_captions(
    draft,
    vo["words_path"],
    output_path=str(BASE / "fitbod_captioned.mp4"),
    fontsize=55,
    caption_style="anton",
)
print(f"      captioned: {captioned}")

# Step 5: Headline
print("\n[5/5] Burning headline (TRANSFORMING MY TRAINING, yellow bg)...")
final = burn_headline(
    captioned,
    "TRANSFORMING MY TRAINING",
    output_path=str(BASE / "fitbod_final.mp4"),
    fontsize=64,
    bg_color="yellow",
    text_color="black",
    font_name="anton",
)
print(f"      final: {final}")

print(f"\n✓ Done → {final}")
