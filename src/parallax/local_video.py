"""Zero-spend ffmpeg video primitives exposed by ``parallax video``."""
from __future__ import annotations
import json
from pathlib import Path
from .ffmpeg_utils import parse_resolution, probe_duration, run_ffmpeg
from .settings import _ASPECT_TO_RESOLUTION

def resolution(aspect: str, requested: str | None = None) -> tuple[int, int]:
    """Resolve an explicit deliverable size, falling back to the aspect preset."""
    return parse_resolution(requested or _ASPECT_TO_RESOLUTION[aspect])

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

def assemble(shots: list[dict], out: str, *, aspect="9:16", resolution_: str | None=None, fps=30, voice: str|None=None, voice_offset=0., captions=False, words=None, caption_style="anton", caption_y: float=.65, report: str | None=None) -> str:
    """Assemble local shots. Clip manifest entries accept `trim_start`, output
    `duration`, and `speed`; e.g. seconds 1.5–4.0 at 1.25x is
    `{\"path\": \"clip.mp4\", \"trim_start\": 1.5, \"duration\": 2.0, \"speed\": 1.25}`.
    """
    import tempfile
    from .captions import burn_captions
    w,h=resolution(aspect, resolution_); outp=Path(out); outp.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="parallax-assemble-") as td:
        clips=[]
        for i,s in enumerate(shots):
            path=str(s["path"]); dur=float(s.get("duration") or probe_duration(path) or 5); cp=Path(td)/f"{i}.mp4"
            if Path(path).suffix.lower() in {".png",".jpg",".jpeg",".webp"}:
                # ken_burns accepts aspect presets only; explicitly scale its output below.
                ken_burns(path,dur,str(cp),move=s.get("move","in"),aspect=aspect,fps=fps)
                if resolution_:
                    scaled=Path(td)/f"{i}-scaled.mp4"
                    r=run_ffmpeg(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(cp),"-vf",f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={fps},setsar=1","-an","-frames:v",str(round(dur*fps)),"-c:v","libx264","-pix_fmt","yuv420p",str(scaled)],capture_output=True,text=True)
                    if r.returncode: raise RuntimeError(r.stderr[:500])
                    cp=scaled
            else:
                start=float(s.get("trim_start",0)); speed=float(s.get("speed",1)); vf=f"setpts=PTS/{speed},scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={fps},setsar=1"
                r=run_ffmpeg(["ffmpeg","-y","-hide_banner","-loglevel","error","-ss",str(start),"-i",path,"-t",str(dur*speed),"-vf",vf,"-an","-t",str(dur),"-c:v","libx264","-pix_fmt","yuv420p",str(cp)],capture_output=True,text=True)
                if r.returncode: raise RuntimeError(r.stderr[:500])
            clips.append(cp)
        total_frames=sum(max(1, round(float(s.get("duration") or probe_duration(str(s["path"])) or 5)*fps)) for s in shots)
        total=total_frames/fps
        manifest=Path(td)/"concat.txt"; manifest.write_text("".join(f"file '{p}'\n" for p in clips))
        base=Path(td)/"base.mp4"
        cmd=["ffmpeg","-y","-hide_banner","-loglevel","error","-f","concat","-safe","0","-i",str(manifest)]
        if voice:
            # Input timestamps are not honoured after a complex graph. Delay samples here.
            delay=max(0,round(float(voice_offset)*1000))
            cmd += ["-i",voice,"-filter_complex",f"[1:a]adelay={delay}:all=1,apad,atrim=duration={total:.6f}[a]","-map","0:v","-map","[a]"]
        cmd += ["-t",f"{total:.6f}","-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac",str(base)]
        r=run_ffmpeg(cmd,capture_output=True,text=True)
        if r.returncode: raise RuntimeError(r.stderr[:500])
        if captions and words: return burn_captions(str(base), words, str(outp), caption_style=caption_style, shift_s=voice_offset, caption_y=caption_y, report_path=report)
        base.replace(outp)
    return str(outp)

def overlay_display_text(text: str, preset: str, t: float, duration: float) -> str:
    """Pure timeline used by the renderer and tests."""
    if preset == "typewriter": return text[:min(len(text), max(0, int(t / max(.01, duration*.6) * len(text))))]
    if preset == "count-up":
        import re
        match=re.search(r"[-+]?\d+(?:\.\d+)?", text)
        if match:
            target=float(match.group()); value=target*min(1, max(0,t)/max(.01,duration*.6))
            shown=str(int(value)) if target.is_integer() else f"{value:.1f}"
            return text[:match.start()]+shown+text[match.end():]
    return text

def text_overlay(source: str, text: str, duration: float, out: str, *, preset="pop", position="center", y: float | None=None, size: int=64, color: str="#ffffff", aspect="9:16", resolution_: str | None=None) -> str:
    """A small local kinetic-text renderer.  It deliberately uses bundled fonts."""
    import tempfile
    from PIL import Image, ImageDraw
    from .captions.layout import layout_caption, _font
    from .captions.styles import resolve_caption_style, _FONTS_DIR
    w,h=resolution(aspect, resolution_); style=resolve_caption_style("anton")
    layout=layout_caption(text,w,h,size,style)
    if position == "upper": layout["y"] = max(layout["safe"]["top"], h//3)
    elif position == "lower": layout["y"] = min(layout["safe"]["bottom"]-(layout["box"]["bottom"]-layout["box"]["top"]), h*2//3)
    if y is not None:
        bh=layout["box"]["bottom"]-layout["box"]["top"]; cy=max(layout["safe"]["top"]+bh/2,min(layout["safe"]["bottom"]-bh/2,float(y)*h)); layout["y"]=round(cy-bh/2)
    with tempfile.TemporaryDirectory() as td:
        base=Path(td)/"base.mp4"
        if Path(source).suffix.lower() in {".png",".jpg",".jpeg",".webp"}: ken_burns(source,duration,str(base),aspect=aspect)
        else:
            r=run_ffmpeg(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",source,"-t",str(duration),"-vf",f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps=30,setsar=1","-an",str(base)],capture_output=True,text=True)
            if r.returncode: raise RuntimeError(r.stderr[:500])
        frames=Path(td)/"frames"; frames.mkdir(); count=max(1,round(duration*30))
        for n in range(count):
            t=n/30; shown=overlay_display_text(text,preset,t,duration); scale=1.
            if preset == "pop":
                scale=.6+.48*min(1,t/.18) if t < .18 else 1.08-.08*min(1,(t-.18)/.17)
            font_obj=_font(style,max(8,round(layout["fontsize"]*scale))); layer=Image.new("RGBA",(w,h)); draw=ImageDraw.Draw(layer)
            bbox=draw.multiline_textbbox((0,0),shown,font=font_obj,stroke_width=3,align="center")
            tw,th=bbox[2]-bbox[0],bbox[3]-bbox[1]; x=max(layout["safe"]["left"],min(layout["safe"]["right"]-tw,(w-tw)//2)); yy=max(layout["safe"]["top"], min(layout["safe"]["bottom"]-th, layout["y"]))
            if preset == "slide-up": yy=min(layout["safe"]["bottom"]-th, yy+round(max(0,.35-t)*120))
            draw.multiline_text((x,yy),shown,font=font_obj,fill=color,stroke_width=3,stroke_fill="black",align="center")
            layer.save(frames/f"{n:06d}.png")
        r=run_ffmpeg(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(base),"-framerate","30","-i",str(frames/"%06d.png"),"-filter_complex",f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps=30[b];[b][1:v]overlay=0:0:shortest=1","-frames:v",str(count),"-an","-c:v","libx264","-pix_fmt","yuv420p",out],capture_output=True,text=True)
        if r.returncode: raise RuntimeError(r.stderr[:500])
    return out
