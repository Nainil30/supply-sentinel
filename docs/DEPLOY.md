# Deploy the hosted demo (owner steps)

The hosted demo runs the Planner Console in replay mode only (`--hosted`), so it needs no secrets.

## Streamlit Community Cloud

1. Make sure `main` is up to date on GitHub.
2. Go to share.streamlit.io, sign in with GitHub, "Create app", pick this repo, branch `main`.
3. Main file path: `hosted_app.py`. Python 3.12. Dependencies come from `pyproject.toml` / `uv.lock` (verify current free-tier support at deploy time).
4. Do not add any secrets. Deploy.
5. Paste the URL into the README (`Hosted demo` link) and commit.

`hosted_app.py` builds the base snapshot on first start (seeded, deterministic), sets `SENTINEL_HOSTED=1` and the replay profile, then runs the console.

## Hugging Face Space (alternative)

New Space, SDK Docker, push this repo. The Dockerfile's default command serves the console in hosted replay mode on port 8501; set `app_port: 8501` in the Space README front matter.

## GIF

`docs/demo.gif` (in the README) is a six-page tour captured from the console in replay mode. To re-record, follow `docs/DEMO_SCRIPT.md` with any screen recorder and save over it.
