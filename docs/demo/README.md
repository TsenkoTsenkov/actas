# Demo recording

`render_demo.py` draws the demo embedded at the top of the [README](../../README.md)
(`actas-demo.gif`, about 83 seconds, 1280x760) and a sharper `actas-demo.mp4`, frame by
frame. All names, accounts and URLs are dummy data. The terminal lines follow real `actas`
output (`status`, `status --why`, the run badge, the `actas-stop:` refusal, the typed
confirmation), shortened to fit: some suffixes such as `(pattern …)` are dropped and the run
badge is wrapped onto two lines. The desktop-app scene is an illustrative mock-up, not a
screenshot.

Scenes: one manifest of profiles and roles; two terminals side by side as different accounts;
any agent or CLI; `status --why`; an agent in a client-a session pushing as `oss` (allowed by
`agents_may_use: ["*"]`) and then asking for a `confirm` profile and being refused; the human
approving in their own terminal, with all three decisions in the audit log.

```sh
pip install pillow          # and ffmpeg on PATH for the MP4
python3 docs/demo/render_demo.py docs/demo/out
cp docs/demo/out/actas-demo.gif docs/demo/out/actas-demo.mp4 docs/demo/
```

`docs/demo/out/` is git-ignored. Rendering needs macOS system fonts (Menlo, SF, Helvetica
Neue); elsewhere it silently falls back to Pillow's small bitmap font. When actas's output
format changes, update the matching lines in `render_demo.py` and re-render.
