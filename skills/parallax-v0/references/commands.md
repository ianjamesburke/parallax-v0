# Model selection and utility commands

Use `parallax models list` and `parallax models show` for the installed catalog. Catalog costs are estimates; budget against billed receipts and include retries.

## Standalone commands

```bash
# Image generation
parallax image generate "prompt" --aspect 9:16 --model grok-image
parallax image generate "prompt" --ref ./face.png       # with reference
parallax image generate "prompt" --size 480x854          # explicit WxH
parallax image analyze ./frame.png "color palette?"

# Voiceover synthesis
parallax audio voiceover --text "Hello world" --out /tmp/vo.mp3 --voice Fenrir --voice-model tts-gemini
parallax audio voiceover --text "Hello world" --out /tmp/vo.mp3 --speed 1.2  # with atempo

# Video generation
parallax video animate --prompt "Slow zoom in" --start ./frame.png --model mid --out ./clip.mp4
parallax video animate --prompt "Motion" --ref ./ref.png --duration 5

# Audio utilities
parallax audio transcribe ./clip.mp4
parallax audio detect-silences ./clip.mp4
parallax audio trim ./clip.mp4 --start 1.5 --end 8.0
parallax audio cap-pauses       # cap inter-word gaps (word-driven, not amplitude)
parallax audio pad-onsets        # ensure minimum lead-in silence per word
parallax audio speed ./clip.mp3 --rate 1.2   # or --by 20%

# Video utilities
parallax video frame ./clip.mp4 --at 2.5     # extract frame at timestamp
parallax video color ./clip.mp4              # sample pixel color → 0xRRGGBB

# Index footage for search
parallax ingest ./clips/
parallax ingest video.mov --estimate         # dry-run cost report

# Cost + credits
parallax credits          # OpenRouter balance
parallax usage            # spend by model/session
parallax log latest       # last run summary
parallax log list         # all runs

# Validation + schema
parallax validate --folder ./project --brief ./project/brief.yaml
parallax validate --folder ./project --plan ./project/parallax/plan.yaml
parallax schema           # human-readable overview of brief + plan fields
parallax schema brief     # JSON schema for brief.yaml
parallax schema plan      # JSON schema for plan.yaml

# Verify suite
parallax verify run                    # run all verify cases
parallax verify scaffold <case-name>   # scaffold a new verify case
```

---

