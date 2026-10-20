"""Zero-spend ffmpeg video primitives exposed by ``parallax video``."""
from __future__ import annotations
import json
from pathlib import Path
from .ffmpeg_utils import parse_resolution, probe_duration, run_ffmpeg
from .settings import _ASPECT_TO_RESOLUTION

def resolution(aspect: str) -> tuple[int, int]: return parse_resolution(_ASPECT_TO_RESOLUTION[aspect])

def ken_burns(image: str, duration: float, out: str, *, move="in", zoom=1.12, aspect="9:16", fps=30) -> str:
    w,h = resolution(aspect); frames=max(1, round(duration*fps)); p=Path(out); p.parent.mkdir(parents=True,exist_ok=True)
    # Scale before zoompan: avoids the one-pixel rounding jitter seen on source-size zoompan.
    z0, z1 = (zoom, 1.0) if move == "out" else (1.0, zoom)
    pan = {"pan-left":"iw-(iw-ow)*on/(%d-1)"%max(2,frames), "pan-right":"(iw-ow)*on/(%d-1)"%max(2,frames), "pan-up":"(ih-oh)*on/(%d-1)"%max(2,frames), "pan-down":"(ih-oh)*on/(%d-1)"%max(2,frames)}
    x=pan.get(move,"iw/2-(iw/zoom/2)"); y="ih/2-(ih/zoom/2)"
    vf=(f"scale={w*2}:{h*2}:force_original_aspect_ratio=increase,crop={w*2}:{h*2},"
        f"zoompan=z='{z0}+({z1}-{z0})*on/{max(1,frames-1)}':x='{x}':y='{y}':d=1:s={w}x{h}:fps={fps},setsar=1")
    r=run_ffmpeg(["ffmpeg","-y","-hide_banner","-loglevel","error","-loop","1","-i",image,"-vf",vf,"-frames:v",str(frames),"-an","-c:v","libx264","-pix_fmt","yuv420p",str(p)],capture_output=True,text=True)
    if r.returncode: raise RuntimeError(r.stderr[:500])
    return str(p)

def assemble(shots: list[dict], out: str, *, aspect="9:16", fps=30, voice: str|None=None, voice_offset=0., captions=False, words=None, caption_style="anton") -> str:
    """Assemble local shots. Clip manifest entries accept `trim_start`, output
    `duration`, and `speed`; e.g. seconds 1.5–4.0 at 1.25x is
    `{\"path\": \"clip.mp4\", \"trim_start\": 1.5, \"duration\": 2.0, \"speed\": 1.25}`.
    """
    import tempfile
    from .captions import burn_captions
    w,h=resolution(aspect); outp=Path(out); outp.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="parallax-assemble-") as td:
        clips=[]
        for i,s in enumerate(shots):
            path=str(s["path"]); dur=float(s.get("duration") or probe_duration(path) or 5); cp=Path(td)/f"{i}.mp4"
            if Path(path).suffix.lower() in {".png",".jpg",".jpeg",".webp"}:
                ken_burns(path,dur,str(cp),move=s.get("move","in"),aspect=aspect,fps=fps)
            else:
                start=float(s.get("trim_start",0)); speed=float(s.get("speed",1)); vf=f"setpts=PTS/{speed},scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={fps},setsar=1"
                r=run_ffmpeg(["ffmpeg","-y","-hide_banner","-loglevel","error","-ss",str(start),"-i",path,"-t",str(dur*speed),"-vf",vf,"-an","-t",str(dur),"-c:v","libx264","-pix_fmt","yuv420p",str(cp)],capture_output=True,text=True)
                if r.returncode: raise RuntimeError(r.stderr[:500])
            clips.append(cp)
        manifest=Path(td)/"concat.txt"; manifest.write_text("".join(f"file '{p}'\n" for p in clips))
        base=Path(td)/"base.mp4"
        cmd=["ffmpeg","-y","-hide_banner","-loglevel","error","-f","concat","-safe","0","-i",str(manifest)]
        if voice: cmd += ["-itsoffset",str(voice_offset),"-i",voice,"-filter_complex","[1:a]apad[a]","-map","0:v","-map","[a]","-shortest"]
        cmd += ["-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac",str(base)]
        r=run_ffmpeg(cmd,capture_output=True,text=True)
        if r.returncode: raise RuntimeError(r.stderr[:500])
        if captions and words: return burn_captions(str(base), words, str(outp), caption_style=caption_style, shift_s=voice_offset)
        base.replace(outp)
    return str(outp)

def text_overlay(source: str, text: str, duration: float, out: str, *, preset="pop", position="center", aspect="9:16") -> str:
    """A small local kinetic-text renderer.  It deliberately uses bundled fonts."""
    import tempfile
    from .captions.layout import layout_caption
    from .captions.styles import resolve_caption_style, _FONTS_DIR
    w,h=resolution(aspect); style=resolve_caption_style("anton")
    layout=layout_caption(text,w,h,64,style)
    if position == "upper": layout["y"] = max(layout["safe"]["top"], h//3)
    elif position == "lower": layout["y"] = min(layout["safe"]["bottom"]-(layout["box"]["bottom"]-layout["box"]["top"]), h*2//3)
    font=str(_FONTS_DIR/style["fontfile"]); esc=layout["text"].replace("'","’").replace(":","\\:").replace("\n","\\n")
    # Alpha/scale expressions make the first half-second visibly kinetic while preserving safe bounds.
    if preset == "slide-up": y=f"{layout['y']}+max(0,(.35-t)*120)"; alpha="min(1,t/.35)"
    elif preset == "typewriter": esc=f"%{{eif\\:min({len(text)}\\,t*24)\\:d}}" # fallback text remains intentionally simple
    else: y=str(layout["y"]); alpha="min(1,t/.18)"
    with tempfile.TemporaryDirectory() as td:
        base=Path(td)/"base.mp4"
        if Path(source).suffix.lower() in {".png",".jpg",".jpeg",".webp"}: ken_burns(source,duration,str(base),aspect=aspect)
        else:
            r=run_ffmpeg(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",source,"-t",str(duration),"-vf",f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps=30,setsar=1","-an",str(base)],capture_output=True,text=True)
            if r.returncode: raise RuntimeError(r.stderr[:500])
        vf=f"drawtext=fontfile='{font}':text='{esc}':fontsize={layout['fontsize']}:fontcolor=white:borderw=3:bordercolor=black:x={layout['x']}:y={y}:alpha='{alpha}'"
        r=run_ffmpeg(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(base),"-vf",vf,"-an","-c:v","libx264","-pix_fmt","yuv420p",out],capture_output=True,text=True)
        if r.returncode: raise RuntimeError(r.stderr[:500])
    return out
