# Demo recording

`render_demo.py` draws the README demo (GIF + MP4) frame by frame. All names,
accounts and URLs in it are dummy data. Terminal lines mirror real `ccenv` output
(`status`, `status --why`, the run badge, the `ccenv-stop:` refusal, the typed
confirmation); the desktop-app scene is an illustrative mock-up, not a screenshot.

```sh
pip install pillow          # ffmpeg on PATH for the MP4
python3 docs/demo/render_demo.py docs/demo/out
```

It uses macOS system fonts (Menlo, Helvetica Neue). When ccenv's output format
changes, update the matching lines here and re-render.
