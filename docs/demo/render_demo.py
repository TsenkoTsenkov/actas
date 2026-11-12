#!/usr/bin/env python3
"""Render the ccenv demo GIF/MP4 from a scripted storyboard (dummy data only).

Usage: python3 render_demo.py [outdir]
"""
import os
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "out")
OUT.mkdir(parents=True, exist_ok=True)

W, H = 1280, 760
SCALE = 1  # render scale

# ---------------------------------------------------------------- theme
C = {
    "bg": (13, 16, 23), "win": (22, 27, 34), "bar": (31, 36, 48), "border": (48, 54, 61),
    "text": (230, 237, 243), "dim": (139, 148, 158), "g": (63, 185, 80), "y": (210, 153, 34),
    "r": (248, 81, 73), "c": (57, 197, 207), "b": (88, 166, 255), "m": (188, 140, 255),
    "o": (255, 166, 87), "cap": (240, 246, 252), "capbg": (33, 38, 50),
    # desktop mock
    "d_bg": (32, 31, 29), "d_side": (26, 25, 24), "d_text": (236, 233, 225), "d_dim": (160, 155, 145),
    "d_user": (52, 50, 46), "d_tool": (24, 23, 22), "d_accent": (217, 119, 87), "d_border": (62, 60, 56),
}


def font(path, size, index=0):
    try:
        return ImageFont.truetype(path, size, index=index)
    except OSError:
        return ImageFont.load_default()


MONO = font("/System/Library/Fonts/Menlo.ttc", 16)
MONO_B = font("/System/Library/Fonts/Menlo.ttc", 16, index=1)
MONO_S = font("/System/Library/Fonts/Menlo.ttc", 14)
UI = font("/System/Library/Fonts/SFNS.ttf", 17)
UI_S = font("/System/Library/Fonts/SFNS.ttf", 14)
UI_B = font("/System/Library/Fonts/HelveticaNeue.ttc", 18, index=1)
TITLE = font("/System/Library/Fonts/HelveticaNeue.ttc", 54, index=1)
SUB = font("/System/Library/Fonts/HelveticaNeue.ttc", 24)
CAP = font("/System/Library/Fonts/HelveticaNeue.ttc", 21, index=1)

LH = 22  # terminal line height
CW = MONO.getlength("M")

TAG = re.compile(r"\[(/?)([a-z_]*)\]")


def parse(markup, default="text"):
    """'[g]ok[/] plain' -> [(text, color, bold)]"""
    segs, stack, pos = [], [default], 0
    bold = False
    for m in TAG.finditer(markup):
        if m.start() > pos:
            segs.append((markup[pos:m.start()], stack[-1], bold))
        closing, name = m.group(1), m.group(2)
        if name == "bold":
            bold = not closing
        elif closing:
            if len(stack) > 1:
                stack.pop()
        else:
            stack.append(name)
        pos = m.end()
    if pos < len(markup):
        segs.append((markup[pos:], stack[-1], bold))
    return segs


def plain(markup):
    return TAG.sub("", markup)


# ---------------------------------------------------------------- frames
FRAMES = []  # (PIL.Image, duration_ms)


def emit(img, ms):
    FRAMES.append((img, int(ms)))


# ---------------------------------------------------------------- widgets
class Term:
    def __init__(self, x, y, w, h, title, cwd="~", prompt_color="c"):
        self.x, self.y, self.w, self.h, self.title = x, y, w, h, title
        self.lines = []
        self.cwd = cwd
        self.pc = prompt_color

    def prompt(self):
        return f"[{self.pc}]{self.cwd}[/] [m]❯[/] "

    def rows(self):
        return int((self.h - 46) // LH)

    def draw(self, d, cursor=False):
        x, y, w, h = self.x, self.y, self.w, self.h
        d.rounded_rectangle([x, y, x + w, y + h], 12, fill=C["win"], outline=C["border"])
        d.rounded_rectangle([x, y, x + w, y + 34], 12, fill=C["bar"])
        d.rectangle([x, y + 22, x + w, y + 34], fill=C["bar"])
        for i, col in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
            d.ellipse([x + 14 + i * 20, y + 11, x + 26 + i * 20, y + 23], fill=col)
        tw = UI_S.getlength(self.title)
        d.text((x + w / 2 - tw / 2, y + 9), self.title, font=UI_S, fill=C["dim"])
        vis = self.lines[-self.rows():]
        ty = y + 44
        for i, line in enumerate(vis):
            cx = x + 16
            for text, col, bold in parse(line):
                f = MONO_B if bold else MONO
                d.text((cx, ty), text, font=f, fill=C[col])
                cx += f.getlength(text)
            if cursor and i == len(vis) - 1:
                d.rectangle([cx + 1, ty + 2, cx + CW - 1, ty + LH - 3], fill=C["text"])
            ty += LH


class Scene:
    def __init__(self, caption=None):
        self.widgets = []
        self.caption = caption
        self.cursor_on = None
        self.extra = []  # callables(draw, img)

    def render(self):
        img = Image.new("RGB", (W, H), C["bg"])
        d = ImageDraw.Draw(img)
        for wdg in self.widgets:
            wdg.draw(d, cursor=(wdg is self.cursor_on))
        for fn in self.extra:
            fn(d, img)
        if self.caption:
            num, _, text = self.caption.partition("|")
            cw = CAP.getlength(plain(text))
            total = cw + 44
            bx = W / 2 - total / 2 - 22
            d.rounded_rectangle([bx, H - 58, W / 2 + total / 2 + 22, H - 16], 21, fill=C["capbg"],
                                outline=C["border"])
            d.ellipse([bx + 14, H - 51, bx + 42, H - 23], fill=C["c"])
            nw = CAP.getlength(num)
            d.text((bx + 28 - nw / 2, H - 49), num, font=CAP, fill=C["bg"])
            cx = bx + 54
            for t_, col, _ in parse(text, "cap"):
                d.text((cx, H - 49), t_, font=CAP, fill=C[col])
                cx += CAP.getlength(t_)
        return img

    def snap(self, ms):
        emit(self.render(), ms)


def type_cmd(scene, term, cmd, cps=28, hold=350):
    """Type a command after the prompt, char by char."""
    scene.cursor_on = term
    term.lines.append(term.prompt())
    scene.snap(hold)
    base = term.lines[-1]
    typed, _, comment = cmd.partition("[dim]")
    step = 2
    for i in range(0, len(typed), step):
        term.lines[-1] = base + typed[: i + step]
        scene.snap(1000 * step / cps)
    if comment:
        term.lines[-1] = base + typed + "[dim]" + comment
    scene.snap(300)
    scene.cursor_on = None


def out(scene, term, lines, per=70, hold=0):
    for ln in lines:
        term.lines.append(ln)
        scene.snap(per)
    if hold:
        scene.snap(hold)


# ---------------------------------------------------------------- scenes
def title_card(title, sub, sub2=None, ms=2600):
    img = Image.new("RGB", (W, H), C["bg"])
    d = ImageDraw.Draw(img)
    tw = TITLE.getlength(title)
    d.text((W / 2 - tw / 2, H / 2 - 90), title, font=TITLE, fill=C["text"])
    sw = SUB.getlength(sub)
    d.text((W / 2 - sw / 2, H / 2 - 10), sub, font=SUB, fill=C["c"])
    if sub2:
        s2 = SUB.getlength(sub2)
        d.text((W / 2 - s2 / 2, H / 2 + 34), sub2, font=SUB, fill=C["dim"])
    emit(img, ms)


def scene_manifest():
    s = Scene("1|One manifest: [c]profiles[/cap] for each context, built from reusable [c]roles[/cap]")
    t = Term(40, 24, W - 80, H - 110, "~/.config/ccenv/environments.yaml")
    s.widgets.append(t)
    y = [
        "[dim]version:[/] [o]2[/]",
        "[b]defaults:[/] { [b]git[/]: { [b]name[/]: Ada Example, [b]email[/]: ada@example.com } }",
        "[b]roles:[/]                                      [dim]# reusable pieces, merged with use:[/]",
        "  [y]aws-ro[/]:    { [b]level[/]: readonly, [b]aws[/]: { [b]profile[/]: personal-readonly, [b]region[/]: eu-west-1 } }",
        "  [y]aws-power[/]: { [b]level[/]: power,    [b]aws[/]: { [b]profile[/]: personal-poweruser } }",
        "  [y]gh-ro[/]:     { [b]level[/]: readonly, [b]github[/]: { [b]auth[/]: [g]\"sops:personal/gh_ro_pat\"[/] } }",
        "  [y]corp-gateway[/]:",
        "    [b]claude[/]: { [b]provider[/]: gateway, [b]base_url[/]: https://llm.corp.example,",
        "              [b]token[/]: [g]\"keychain:ccenv/corp-gateway\"[/] }   [dim]# a ref, never the value[/]",
        "",
        "[b]profiles:[/]",
        "  [c]personal[/]:                                  [dim]# read-only everywhere[/]",
        "    [b]match[/]: ~/code/personal",
        "    [b]use[/]: [gh-ro, aws-ro]",
        "  [c]personal-power[/]:                            [dim]# same, plus AWS PowerUserAccess[/]",
        "    [b]use[/]: [personal, aws-power]",
        "    [b]confirm[/]: [o]true[/]                            [dim]# a human must approve every use[/]",
        "  [c]client-a[/]:",
        "    [b]match[/]: ~/code/client-a",
        "    [b]use[/]: [corp-gateway]                       [dim]# LLM gateway instead of a subscription[/]",
        "    [b]github[/]: { [b]auth[/]: [g]\"gh:ada-client-a\"[/] }       [dim]# 2nd GitHub account, no auth switch[/]",
        "    [b]aws[/]:    { [b]profile[/]: client-a-dev, [b]region[/]: eu-west-1 }",
        "    [b]git[/]:    { [b]email[/]: ada@client-a.example }",
        "  [c]oss[/]:      { [b]match[/]: ~/code/oss, [b]claude[/]: { [b]home[/]: work } }      [dim]# 2nd claude.ai subscription[/]",
        "  [c]client-b[/]: { [b]match[/]: ~/code/client-b, [b]tool[/]: codex, [b]gcp[/]: { [b]project[/]: client-b-dev } }",
    ]
    s.snap(500)
    for ln in y:
        t.lines.append(ln)
        s.snap(140)
    s.snap(4200)


# The tag parser would treat "[gh-ro, aws-ro]" as markup only if it matched [a-z]+ exactly;
# "gh-ro, aws-ro" contains '-', ',' and ' ' so it is left alone. Same for the others.


def scene_parallel():
    s = Scene("2|Side by side: different subscription, gateway, AWS account and GitHub identity")
    half = (W - 100) // 2
    a = Term(40, 24, half, H - 110, "zsh — ~/code/personal", cwd="~/code/personal")
    b = Term(60 + half, 24, half, H - 110, "zsh — ~/code/client-a", cwd="~/code/client-a")
    s.widgets += [a, b]
    s.snap(400)
    type_cmd(s, a, "ccenv status")
    out(s, a, [
        "profile   [bold]personal[/]  [dim]([/][g]readonly[/][dim]; by pattern)[/]",
        "cwd       ~/code/personal -> personal",
        "claude    subscription",
        "aws       personal-readonly eu-west-1",
        "github    token sops:personal/gh_ro_pat",
        "git       Ada Example <ada@example.com>",
        "",
    ])
    type_cmd(s, b, "ccenv status")
    out(s, b, [
        "profile   [bold]client-a[/]  [dim]([/][y]standard[/][dim]; by pattern)[/]",
        "cwd       ~/code/client-a -> client-a",
        "claude    gateway https://llm.corp.example",
        "aws       client-a-dev eu-west-1",
        "github    gh:ada-client-a",
        "git       Ada Example <ada@client-a.example>",
        "",
    ])
    type_cmd(s, a, "ccenv run", cps=20)
    out(s, a, ["[dim]ccenv:[/] [c]personal[/] · readonly · subscription", "       aws:personal-readonly · gh:pat"], hold=300)
    type_cmd(s, b, "ccenv run", cps=20)
    out(s, b, ["[dim]ccenv:[/] [c]client-a[/] · standard · gateway", "       aws:client-a-dev · gh:ada-client-a"], hold=900)
    a.lines.append("[dim]  … claude starts with these credentials[/]")
    b.lines.append("[dim]  … claude starts with these credentials[/]")
    s.snap(2600)


def scene_any_tool():
    s = Scene("3|Not just Claude: any agent or CLI runs inside a profile")
    t = Term(40, 24, W - 80, H - 110, "zsh — ~/code/client-b", cwd="~/code/client-b")
    s.widgets.append(t)
    s.snap(300)
    type_cmd(s, t, "ccenv run                                  [dim]# this profile's tool is codex[/]")
    out(s, t, ["[dim]ccenv:[/] [c]client-b[/] · standard · codex", ""], hold=900)
    type_cmd(s, t, "ccenv exec client-a -- aws sts get-caller-identity --query Arn")
    out(s, t, ["[g]\"arn:aws:sts::111122223333:assumed-role/AWSReservedSSO_Developer/ada\"[/]", ""], hold=700)
    type_cmd(s, t, "ccenv exec client-a -- gh api user --jq .login")
    out(s, t, ["ada-client-a", ""], hold=700)
    type_cmd(s, t, "eval \"$(ccenv shell --no-secrets)\"            [dim]# or direnv: use ccenv[/]")
    s.snap(2400)


def scene_explain():
    s = Scene("4|Composable and explainable: see which role set every value")
    t = Term(40, 24, W - 80, H - 110, "zsh — ~/code/personal", cwd="~/code/personal")
    s.widgets.append(t)
    s.snap(300)
    type_cmd(s, t, "ccenv status personal-power --why")
    out(s, t, [
        "profile   [bold]personal-power[/]  [dim]([/][r]power[/][dim], [/][o]confirm[/][dim]; by name)[/]",
        "cwd       ~/code/personal -> personal",
        "          [dim]this directory belongs to personal, not personal-power[/]",
        "claude    subscription",
        "aws       [c]personal-poweruser[/] eu-west-1",
        "github    token sops:personal/gh_ro_pat",
        "git       Ada Example <ada@example.com>",
        "chain     defaults -> gh-ro -> aws-ro -> personal -> aws-power -> personal-power",
        "          aws.profile = [c]personal-poweruser[/] [dim](from aws-power, was personal-readonly from aws-ro)[/]",
        "          aws.region = eu-west-1 [dim](from aws-ro)[/]",
        "          git.email = ada@example.com [dim](from defaults)[/]",
        "          github.auth = sops:personal/gh_ro_pat [dim](from gh-ro)[/]",
        "env.set   AWS_PROFILE, AWS_REGION, CCENV, CCENV_LEVEL, GH_CONFIG_DIR, GIT_CONFIG_* …",
        "env.secret GH_TOKEN, GITHUB_TOKEN   [dim]# names only, values come from sops at launch[/]",
        "rules     27 deny, 23 ask   [dim]# rendered into Claude Code permissions[/]",
    ], per=110, hold=4200)


def wrap(text, f, width):
    words, lines, cur = text.split(" "), [], ""
    for w_ in words:
        trial = (cur + " " + w_).strip()
        if f.getlength(plain(trial)) <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = w_
    if cur:
        lines.append(cur)
    return lines


class Desktop:
    """Stylized, generic mock of an agent chat app (illustration only)."""

    def __init__(self):
        self.items = []  # dicts: kind=user|assistant|tool
        self.x, self.y, self.w, self.h = 40, 24, W - 80, H - 110

    def draw(self, d, cursor=False):
        x, y, w, h = self.x, self.y, self.w, self.h
        d.rounded_rectangle([x, y, x + w, y + h], 12, fill=C["d_bg"], outline=C["d_border"])
        d.rounded_rectangle([x, y, x + 230, y + h], 12, fill=C["d_side"])
        d.rectangle([x + 218, y, x + 230, y + h], fill=C["d_side"])
        for i, col in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
            d.ellipse([x + 14 + i * 20, y + 13, x + 26 + i * 20, y + 25], fill=col)
        d.text((x + 18, y + 50), "Code", font=UI_B, fill=C["d_text"])
        for i, (name, active) in enumerate([("personal-site", True), ("infra", False), ("client-a-api", False)]):
            yy = y + 92 + i * 34
            if active:
                d.rounded_rectangle([x + 10, yy - 6, x + 218, yy + 24], 8, fill=C["d_user"])
            d.text((x + 22, yy), name, font=UI_S, fill=C["d_text"] if active else C["d_dim"])
        d.text((x + 18, y + h - 34), "illustrative mock-up", font=UI_S, fill=C["d_dim"])
        # header
        d.text((x + 256, y + 14), "personal-site", font=UI_B, fill=C["d_text"])
        d.text((x + 256 + UI_B.getlength("personal-site") + 12, y + 17),
               "~/code/personal/site · desktop app (no ccenv run)", font=UI_S, fill=C["d_dim"])
        d.line([x + 230, y + 46, x + w, y + 46], fill=C["d_border"])
        # input
        d.rounded_rectangle([x + 256, y + h - 66, x + w - 24, y + h - 18], 14, fill=C["d_user"],
                            outline=C["d_border"])
        d.text((x + 274, y + h - 52), "Reply…", font=UI, fill=C["d_dim"])
        # chat (bottom-anchored layout, render from the top with clipping)
        col_x, col_w = x + 270, w - 330
        blocks = []
        for it in self.items:
            if it["kind"] == "user":
                ls = wrap(it["text"], UI, col_w * 0.62)
                bw = max(UI.getlength(l) for l in ls) + 30
                blocks.append(("user", ls, bw, len(ls) * 24 + 22))
            elif it["kind"] == "assistant":
                ls = []
                for para in it["text"].split("\n"):
                    ls += wrap(para, UI, col_w) if para else [""]
                blocks.append(("assistant", ls, col_w, len(ls) * 24 + 8))
            else:
                ls = it["out"]
                blocks.append(("tool", (it["cmd"], ls, it.get("status", "")), col_w, 40 + len(ls) * 20 + 14))
        main_d = d
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        total = sum(b[3] + 14 for b in blocks)
        top, bottom = y + 60, y + h - 80
        cy = max(top, bottom - total) if total > bottom - top else top
        if total > bottom - top:
            cy = bottom - total
        for kind, data, bw, bh in blocks:
            if cy + bh < top:
                cy += bh + 14
                continue
            if kind == "user":
                bx = col_x + col_w - bw
                d.rounded_rectangle([bx, cy, bx + bw, cy + bh], 14, fill=C["d_user"])
                for i, l in enumerate(data):
                    d.text((bx + 15, cy + 11 + i * 24), l, font=UI, fill=C["d_text"])
            elif kind == "assistant":
                d.ellipse([col_x - 26, cy + 5, col_x - 12, cy + 19], fill=C["d_accent"])
                for i, l in enumerate(data):
                    cx = col_x
                    for text, colr, _ in parse(l, "d_text"):
                        d.text((cx, cy + i * 24), text, font=UI, fill=C[colr])
                        cx += UI.getlength(text)
            else:
                cmd, ls, status = data
                d.rounded_rectangle([col_x, cy, col_x + bw, cy + bh], 10, fill=C["d_tool"], outline=C["d_border"])
                d.text((col_x + 14, cy + 10), "Bash", font=UI_S, fill=C["d_dim"])
                d.text((col_x + 58, cy + 9), cmd, font=MONO_S, fill=C["d_text"])
                if status:
                    sc = C["r"] if "exit 3" in status else C["g"]
                    d.text((col_x + bw - UI_S.getlength(status) - 14, cy + 10), status, font=UI_S, fill=sc)
                for i, l in enumerate(ls):
                    cx = col_x + 14
                    for text, colr, _ in parse(l, "d_dim"):
                        d.text((cx, cy + 38 + i * 20), text, font=MONO_S, fill=C[colr])
                        cx += MONO_S.getlength(text)
            cy += bh + 14
        box = (int(x + 231), int(top - 6), int(x + w - 1), int(bottom - 4))
        crop = layer.crop(box)
        main_d._image.paste(crop, box[:2], crop)


def stream(scene, desk, text, wps=14):
    item = {"kind": "assistant", "text": ""}
    desk.items.append(item)
    words = text.split(" ")
    for i in range(len(words)):
        item["text"] = " ".join(words[: i + 1])
        if i % 2 == 1 or i == len(words) - 1:
            scene.snap(2000 / wps)


def scene_desktop():
    s = Scene("5|In the desktop app, an agent asks for stronger credentials and is refused")
    desk = Desktop()
    s.widgets.append(desk)
    s.snap(500)
    desk.items.append({"kind": "user", "text": "The plan looks good. Go ahead and apply the terraform change to the staging bucket."})
    s.snap(1300)
    stream(s, desk, "I'll check which credentials this session has first.")
    tool = {"kind": "tool", "cmd": "ccenv status --json", "out": [], "status": ""}
    desk.items.append(tool)
    s.snap(500)
    for ln in ['{"ok": true, "session": {"profile": "personal", "source": "env"}, "profile": "personal",',
               ' "level": "readonly", "identities": {"aws": {"profile": "personal-readonly", …}}, …}']:
        tool["out"].append(ln)
        s.snap(250)
    tool["status"] = "exit 0"
    s.snap(900)
    stream(s, desk, "This session is read-only. Applying needs the personal-power profile, so I'll request it.")
    tool2 = {"kind": "tool", "cmd": "ccenv exec personal-power --reason \"apply reviewed plan\" -- terraform apply", "out": [], "status": ""}
    desk.items.append(tool2)
    s.snap(700)
    for ln in ["[r]ccenv-stop: code=needs_user reason=confirm profile=personal-power[/]",
               "ccenv: refused: personal-power requires a typed confirmation, which only",
               "a human in a terminal can give. … The agent wants to run `terraform apply`",
               "as ccenv profile personal-power (power; aws:personal-poweruser, gh:pat).",
               "Agent's reason: \"apply reviewed plan\" … run it yourself in a separate",
               "terminal:  [d_text]ccenv exec personal-power -- terraform apply[/]"]:
        tool2["out"].append(ln)
        s.snap(300)
    tool2["status"] = "exit 3 · needs you"
    s.snap(1100)
    stream(s, desk, "ccenv stopped me: personal-power needs your approval, and I can't grant that myself. "
                    "If you agree, run this in your own terminal:\n[d_accent]ccenv exec personal-power -- terraform apply[/]\n"
                    "I'll wait. I won't retry or try other credentials.")
    s.snap(4200)


def scene_human():
    s = Scene("6|The human approves in their own terminal, and every decision is audit-logged")
    t = Term(40, 24, W - 80, H - 110, "zsh — ~/code/personal/site", cwd="~/code/personal/site")
    s.widgets.append(t)
    s.snap(300)
    type_cmd(s, t, "ccenv exec personal-power -- terraform apply")
    out(s, t, ["[y]Elevate this shell -> personal-power (power) to run: terraform apply.[/]"], hold=200)
    t.lines.append("Type the profile name to continue: ")
    s.cursor_on = t
    s.snap(700)
    base = t.lines[-1]
    word = "personal-power"
    for i in range(1, len(word) + 1):
        t.lines[-1] = base + word[:i]
        s.snap(70)
    s.snap(500)
    s.cursor_on = None
    out(s, t, [
        "",
        "Terraform will perform the following actions:",
        "  [y]~[/] aws_s3_bucket_versioning.site  [dim](staging)[/]",
        "[g]Apply complete! Resources: 0 added, 1 changed, 0 destroyed.[/]",
        "",
    ], per=160, hold=500)
    type_cmd(s, t, "tail -n 2 ~/.local/state/ccenv/audit.jsonl | jq -c '{profile,decision,agent:.agent.detected}'")
    out(s, t, [
        '{"profile":"personal-power","decision":"refused-needs-user","agent":true}',
        '{"profile":"personal-power","decision":"confirmed-tty","agent":false}',
    ], per=250, hold=3800)


def end_card():
    title_card("ccenv", "subscriptions · gateways · cloud · GitHub · agents, per project",
               "credentials are the boundary; ccenv is the seatbelt", ms=3800)


# ---------------------------------------------------------------- main
title_card("ccenv", "one manifest for every identity you work as",
           "Claude Code · Codex · Gemini · aws · gcloud · az · gh · git", ms=2800)
scene_manifest()
scene_parallel()
scene_any_tool()
scene_explain()
scene_desktop()
scene_human()
end_card()

print(f"{len(FRAMES)} frames, {sum(ms for _, ms in FRAMES) / 1000:.1f}s")

# ---- GIF (Pillow, variable frame durations, one global palette from samples)
gif = OUT / "ccenv-demo.gif"
sample = Image.new("RGB", (W, H * 4))
for i, idx in enumerate([len(FRAMES) // 8, len(FRAMES) // 3, len(FRAMES) * 2 // 3, len(FRAMES) - 30]):
    sample.paste(FRAMES[idx][0], (0, H * i))
pal = sample.quantize(colors=128, method=Image.Quantize.MEDIANCUT)
q = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f, _ in FRAMES]
q[0].save(gif, save_all=True, append_images=q[1:], duration=[ms for _, ms in FRAMES],
          loop=0, optimize=False, disposal=1)
print(f"wrote {gif} ({gif.stat().st_size / 1e6:.1f} MB)")

# ---- MP4 via ffmpeg concat (crisper, much smaller)
fdir = OUT / "frames"
fdir.mkdir(exist_ok=True)
for p in fdir.glob("*.png"):
    p.unlink()
lst = []
for i, (img, ms) in enumerate(FRAMES):
    p = fdir / f"{i:05d}.png"
    img.save(p)
    lst.append(f"file '{p}'\nduration {ms / 1000:.3f}")
lst.append(f"file '{fdir / f'{len(FRAMES) - 1:05d}.png'}'")
(OUT / "frames.txt").write_text("\n".join(lst) + "\n")
mp4 = OUT / "ccenv-demo.mp4"
r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(OUT / "frames.txt"),
                    "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-crf", "20", "-movflags", "+faststart", str(mp4)])
if r.returncode == 0:
    print(f"wrote {mp4} ({mp4.stat().st_size / 1e6:.1f} MB)")
