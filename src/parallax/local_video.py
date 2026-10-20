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

def _graphic_frames(duration, out, draw_frame, *, resolution_="1080x1920"):
    """Render a safe-area-aware, silent 30fps MP4 from PIL frames."""
    import tempfile
    w,h=parse_resolution(resolution_); count=max(1, round(float(duration)*30)); target=Path(out); target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="parallax-graphic-") as td:
        frames=Path(td)/"frames"; frames.mkdir()
        for n in range(count): draw_frame(n/max(1,count-1),w,h).save(frames/f"{n:06d}.png")
        r=run_ffmpeg(["ffmpeg","-y","-hide_banner","-loglevel","error","-framerate","30","-i",str(frames/"%06d.png"),"-frames:v",str(count),"-an","-c:v","libx264","-pix_fmt","yuv420p",str(target)],capture_output=True,text=True)
        if r.returncode: raise RuntimeError(r.stderr[:500])
    return str(target)

def app_screen(screen, duration, out, *, bg="#272137", bg_image=None, callout=None, highlight=None, zoom_to_highlight=False, accent="#721CFF", title=None, resolution_="1080x1920"):
    from PIL import Image, ImageDraw, ImageFont
    src=Image.open(screen).convert("RGB")
    def frame(t,w,h):
        im=Image.new("RGB",(w,h),bg)
        if bg_image: im.paste(Image.open(bg_image).convert("RGB").resize((w,h)))
        d=ImageDraw.Draw(im); safe=(int(w*.08),int(h*.10),int(w*.92),int(h*.90)); top=int(h*.18 if title else h*.10); pw=int(w*.72*(1+.035*t)); ph=int(pw*src.height/src.width); x=(w-pw)//2; y=top+int((h-top-ph)*.5+10*__import__('math').sin(t*6.28))
        d.rounded_rectangle((x-12,y-12,x+pw+12,y+ph+12),radius=42,fill="#101018",outline=accent,width=3); im.paste(src.resize((pw,ph)),(x,y))
        font=ImageFont.truetype(str(Path(__file__).parent/'fonts'/'Montserrat-Black.ttf'),max(28,w//28))
        if title: d.text((safe[0],int(h*.10)),title,font=font,fill="white")
        if highlight:
            hx,hy,hw,hh=highlight; box=(x+hx*pw,y+hy*ph,x+(hx+hw)*pw,y+(hy+hh)*ph); d.rounded_rectangle(box,radius=16,outline=accent,width=max(4,int(8*(.5+.5*t))))
            if callout:
                cy=max(safe[1],int(box[1]-70)); d.rounded_rectangle((safe[0],cy,min(safe[2],safe[0]+w*.72),cy+55),radius=18,fill=accent); d.text((safe[0]+16,cy+10),callout,font=font,fill="white")
        return im
    return _graphic_frames(duration,out,frame,resolution_=resolution_)

def end_card(logo,duration,out,*,icon=None,headline="Become a morning person",cta="Try RISE free",sub="Download on the App Store",bg="#14102A",accent="#721CFF",colors=None,resolution_="1080x1920"):
    from PIL import Image, ImageDraw, ImageFont
    mark=Image.open(logo).convert("RGBA")
    def frame(t,w,h):
        im=Image.new("RGB",(w,h),bg); d=ImageDraw.Draw(im); f=ImageFont.truetype(str(Path(__file__).parent/'fonts'/'Montserrat-Black.ttf'),max(28,w//19)); s=.65+.35*min(1,t/.3); mw=int(w*.58*s); mh=max(1,int(mark.height*mw/mark.width)); im.paste(mark.resize((mw,mh)),((w-mw)//2,int(h*.22)),mark.resize((mw,mh)))
        d.multiline_text((w*.1,int(h*(.43+.04*(1-min(1,t/.35))))),headline,font=f,fill="white",anchor="ma",align="center",spacing=10)
        by=int(h*.67); pulse=1+.035*max(0,__import__('math').sin(t*12)); bw=int(w*.65*pulse); x=(w-bw)//2; d.rounded_rectangle((x,by,x+bw,by+92),radius=46,fill=accent); d.text((w//2,by+46),cta,font=f,fill="white",anchor="mm"); d.text((w//2,by+130),sub,font=ImageFont.truetype(str(Path(__file__).parent/'fonts'/'Montserrat-Black.ttf'),max(18,w//42)),fill="white",anchor="ma"); return im
    return _graphic_frames(duration,out,frame,resolution_=resolution_)

def diagram(preset,duration,out,*,label="YOUR WINDOW",value=75,colors=None,bg="#14102A",resolution_="1080x1920"):
    from PIL import Image, ImageDraw, ImageFont
    accent=(colors or "#721CFF").split(",")[0]; fpath=str(Path(__file__).parent/'fonts'/'Montserrat-Black.ttf')
    def frame(t,w,h):
        im=Image.new("RGB",(w,h),bg); d=ImageDraw.Draw(im); f=ImageFont.truetype(fpath,max(26,w//24)); cx,cy=w//2,h//2
        if preset=="meter": d.arc((w*.15,h*.32,w*.85,h*.85),180,360,fill="#55556A",width=24); a=180+180*min(1,float(value)/100)*t; import math; d.line((cx,cy,cx+math.cos(math.radians(a))*w*.29,cy+math.sin(math.radians(a))*w*.29),fill=accent,width=16); txt=f"{value}%"
        elif preset=="curve":
            pts=[(int(w*.12+i*w*.76/60),int(cy+80*__import__('math').sin(i/10))) for i in range(max(2,int(61*t)))]; d.line(pts,fill=accent,width=12); txt=label+"  PEAK / DIP"
        else: d.ellipse((w*.2,h*.25,w*.8,h*.85),outline="white",width=12); d.arc((w*.2,h*.25,w*.8,h*.85),-70,-70+180*t,fill=accent,width=30); txt=label
        d.text((cx,h*.12),txt,font=f,fill="white",anchor="ma"); return im
    return _graphic_frames(duration,out,frame,resolution_=resolution_)
