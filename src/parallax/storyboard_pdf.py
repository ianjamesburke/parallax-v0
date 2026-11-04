"""Self-contained, Unicode storyboard PDF renderer."""
from pathlib import Path

from fpdf import FPDF
from .references import summary


def render_builtin_pdf(brief, assets: Path, review: dict, output: Path, project_name: str) -> None:
    pdf = FPDF(orientation="landscape", unit="mm", format="letter")
    pdf.set_auto_page_break(False)
    fonts = Path(__file__).parent / "fonts"
    pdf.add_font("DejaVu", fname=fonts / "DejaVuSans.ttf")
    pdf.add_font("DejaVu", style="B", fname=fonts / "DejaVuSans-Bold.ttf")
    name = brief.name or brief.title or brief.concept_name or project_name
    pdf.add_page()

    def text(x, y, width, value, size=10, bold=False, color=(35, 40, 45)):
        pdf.set_xy(x, y)
        pdf.set_text_color(*color)
        pdf.set_font("DejaVu", "B" if bold else "", size)
        pdf.multi_cell(width, size * .42, str(value), new_x="LMARGIN", new_y="NEXT", wrapmode="WORD")
        return pdf.get_y()

    # Measure first, then scale content to fit the fixed page, including long briefs.
    def height(width, value, size, bold=False):
        pdf.set_font("DejaVu", "B" if bold else "", size)
        lines = pdf.multi_cell(width, size * .42, str(value), dry_run=True, output="LINES", wrapmode="WORD")
        return max(1, len(lines)) * size * .42

    def cover(scale, draw=False):
        y = 12
        def block(value, size, bold=False, gap=2, color=(35, 40, 45)):
            nonlocal y
            size *= scale
            if draw: text(13, y, 253, value, size, bold, color)
            y += height(253, value, size, bold) + gap * scale
        block(f"{name} — Storyboard", 22, True, 5)
        if brief.visual_style:
            block("Shared visual style", 11, True, 1)
            block(brief.visual_style, 9, gap=3)
        if flags := summary(brief):
            block(flags, 9, gap=4, color=(90, 95, 102))
        block("Scene overview", 11, True, 2)
        widths = [18, 72, 85, 36, 23, 19]
        columns = ["Scene", "Narration", "Generation prompt", "Interval", "Duration", "Min usable"]
        def row(values, header=False):
            nonlocal y
            size = (8 if header else 7.5) * scale
            h = max(height(w - 2, v, size, header) for w, v in zip(widths, values)) + 2 * scale
            x = 13
            if draw:
                pdf.set_draw_color(210, 214, 216)
                pdf.line(x, y + h, x + sum(widths), y + h)
                for w, v in zip(widths, values):
                    text(x + 1, y + scale, w - 2, v, size, header)
                    x += w
            y += h
        row(columns, True)
        for scene, timing in zip(brief.script.scenes, review["timeline"]):
            start, end = float(timing["start_s"]), float(timing["end_s"])
            shorten = lambda s: (s[:72].rstrip() + "…") if len(s) > 73 else s
            row([scene.scene_id.replace('_', ' '), shorten(' '.join(scene.vo_text.split())), shorten(' '.join(scene.prompt.split())), f"{start:.1f} – {end:.1f} s", f"{end-start:.1f} s", f"{end-start+brief.timing.handle_s:.1f} s"])
        return y
    scale = 1.0
    while cover(scale) > 202 and scale > .1:
        scale *= .95
    cover(scale, True)

    labels = {r.id: r.description for r in brief.references}
    for scene, timing in zip(brief.script.scenes, review["timeline"]):
        pdf.add_page()
        scene_name = scene.scene_id.replace("_", " ")
        try:
            a, b = (float(v) for v in (scene.aspect or brief.aspect).split(":"))
            ratio = a / b if a > 0 and b > 0 else 9 / 16
        except (ValueError, ZeroDivisionError):
            ratio = 9 / 16
        fw, fh = min(74, 175 * ratio), min(175, 74 / ratio)
        fx, fy = 12 + (74 - fw) / 2, 18 + (175 - fh) / 2
        pdf.set_fill_color(237, 239, 240)
        pdf.rect(fx, fy, fw, fh, style="F")
        image = assets / scene.still_path if scene.still_path else None
        if image and image.is_file():
            pdf.image(str(image), x=fx, y=fy, w=fw, h=fh, keep_aspect_ratio=True)
        else:
            text(fx + 2, fy + fh / 2 - 3, fw - 4, f"{scene_name} · no image yet", 8, color=(105, 110, 115))
        start, end = float(timing["start_s"]), float(timing["end_s"])
        items = [(scene_name, 22, True, False), (f"“{scene.vo_text}”", 15, False, False)]
        if scene.storyboard_treatment and scene.storyboard_treatment != scene.prompt:
            items += [("Description", 9, True, True), (scene.storyboard_treatment, 10, False, False)]
        items += [("Generation prompt", 9, True, True), (scene.prompt, 10, False, False),
                  ("Timing", 9, True, True), (f"{start:.1f} – {end:.1f} s · {end-start:.1f} s · minimum usable {end-start+brief.timing.handle_s:.1f} s", 10, False, False),
                  ("Shot / purpose", 9, True, True), (' / '.join(filter(None, (scene.shot_type, scene.purpose))) or '—', 10, False, False)]
        refs = ', '.join(labels[r] for r in scene.refs if r in labels)
        if refs: items.append((f"Refs: {refs}", 9, False, False))
        if scene.on_screen_text: items += [("On-screen text", 9, True, True), (scene.on_screen_text, 10, False, False)]
        scale = 1.0
        def content_height(s):
            return sum(height(175, v, z*s, bold) + (2 if label else 1.5)*s for v,z,bold,label in items)
        while content_height(scale) > 180 and scale > .1:
            scale *= .95
        y = 15
        for value, size, bold, label in items:
            y = text(94, y, 175, value, size * scale, bold, (15, 143, 138) if label else (35, 40, 45)) + (2 if label else 1.5)*scale
        text(12, 204, 180, f"{name} · Storyboard", 7, color=(105, 110, 115))
        text(235, 204, 35, scene_name, 7, color=(105, 110, 115))
    pdf.output(str(output))
