#!/usr/bin/env python3
"""End to end against a local `npm run dev` server: two made-up computers connect through the real device flow,
upload, and the open dashboard picks up a newly routed job by itself. Saves screenshots along the way.

  cd hosted/worker && npm run dev        # in one terminal: http://127.0.0.1:8787
  uv run --no-project --with playwright python hosted/worker/scripts/local_e2e.py OUT_DIR

Everything it writes (router folders, connect.json) goes to a temp folder; your real ~/.config is never touched.
Uses the local dev login, so it only works against a server on 127.0.0.1 without GitHub configured.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[3]
SERVER = os.environ.get("ALLO_ROUTER_SERVER", "http://127.0.0.1:8787")
SITE = os.environ.get("ALLO_SITE")  # optional: a local copy of docs/ (e.g. http://127.0.0.1:8000) for the landing shot
LOGIN = "eudotong"


def cli(folder, cfg, *args, check=True):
    env = {**os.environ, "ALLO_ROUTER_CONFIG_DIR": str(cfg), "ALLO_ROUTER_SERVER": SERVER}
    r = subprocess.run([sys.executable, str(REPO / "allo.py"), "--dir", str(folder), "--no-sync", *args],
                       env=env, capture_output=True, text=True, timeout=120)
    if check and r.returncode != 0:
        raise SystemExit(f"allo-router {' '.join(args)} failed ({r.returncode}):\n{r.stdout}\n{r.stderr}")
    return r


def connect(page, folder, cfg, name, shot=None):
    """Run `allo-router connect` and approve it in the signed-in browser, as a person would."""
    env = {**os.environ, "ALLO_ROUTER_CONFIG_DIR": str(cfg), "PYTHONUNBUFFERED": "1"}  # the link arrives mid-run
    proc = subprocess.Popen([sys.executable, str(REPO / "allo.py"), "--dir", str(folder), "connect", "--no-browser",
                             "--server", SERVER], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    link = None
    for line in proc.stdout:
        m = re.search(r"open (\S+/connect\?code=\S+)", line)
        if m:
            link = m.group(1)
            break
    assert link, "connect printed no approval link"
    page.goto(link)
    page.wait_for_selector("#code:not(:empty)")
    page.fill("input[name=name]", name)
    if shot:
        page.screenshot(path=shot, full_page=True)
    page.click("#approve")
    page.wait_for_selector("#status:not([hidden])")
    out, _ = proc.communicate(timeout=60)
    assert proc.returncode == 0, out
    assert (cfg / "connect.json").exists()
    return out


def main(out_dir):
    out = Path(out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="allo-e2e-"))
    mac, laptop = tmp / "studio-mac", tmp / "work-laptop"
    for d in (mac, laptop):
        subprocess.run([sys.executable, str(Path(__file__).with_name("demo_folder.py")), str(d)], check=True)
    # The laptop ran fewer jobs.
    lines = (laptop / "runs.jsonl").read_text().splitlines()
    (laptop / "runs.jsonl").write_text("\n".join(lines[::3]) + "\n")

    show = cli(mac, tmp / "cfg-mac", "connect", "--show").stdout
    payload = json.loads(show)
    for secret in ("acme", "Acme", "runs/", "claude_work", "api_credit", "Claude (work plan)"):
        assert secret not in show, f"{secret!r} would leave the computer"
    print(f"connect --show: {len(payload['jobs'])} jobs, keys {sorted(payload['jobs'][0])}")

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:  # Playwright's own browser isn't downloaded: use an installed Chrome
            browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        page.goto(f"{SERVER}/signin")
        page.wait_for_selector("#spots-big:not([hidden])")
        page.screenshot(path=out / "signin.png", full_page=True)
        page.fill("input[name=login]", LOGIN)
        page.click("#dev button[type=submit]")
        page.wait_for_url(f"{SERVER}/")

        connect(page, mac, tmp / "cfg-mac", "Studio Mac", shot=out / "device-approval.png")
        connect(page, laptop, tmp / "cfg-laptop", "Work laptop")
        print(cli(laptop, tmp / "cfg-laptop", "upload").stdout.strip())
        print(cli(mac, tmp / "cfg-mac", "upload").stdout.strip())

        page.goto(f"{SERVER}/")
        page.wait_for_selector("h1")
        before = page.inner_text("h1")
        page.screenshot(path=out / "dashboard.png", full_page=True)
        print("dashboard:", before)

        # Live: route and run one more job on the Studio Mac (dry-run backend). run --execute uploads it after the
        # job; the open page should show it within one 15 s poll, without a reload.
        pools = json.loads((mac / "pools.json").read_text())
        for pl in pools["pools"]:
            pl["run"] = {"backend": "dry-run"}
            pl["as_of"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        (mac / "pools.json").write_text(json.dumps(pools, indent=2))
        (mac / "tasks.json").write_text(json.dumps({"tasks": [
            {"id": "acme-live", "name": "Acme live job", "tier": "public", "est_usd": 5, "priority": 1}]}))
        cli(mac, tmp / "cfg-mac", "run")
        ran = cli(mac, tmp / "cfg-mac", "run", "--plan", str(mac / "plan.json"), "--execute", "--yes", check=False)
        assert "uploaded 1 job row" in ran.stdout, ran.stdout + ran.stderr
        t0 = time.time()
        page.wait_for_function("b => document.querySelector('h1').innerText !== b", arg=before, timeout=20000)
        print(f"live update after {time.time() - t0:.1f}s: {page.inner_text('h1')}")

        # The waitlist page as someone past the free spots sees it (the count shown is the real one).
        page.goto(f"{SERVER}/waitlist?login=late-signup")
        page.wait_for_selector("#spots:not([hidden])")
        page.screenshot(path=out / "waitlist.png", full_page=True)

        if SITE:
            page.goto(SITE)
            page.wait_for_selector("[data-spots]:not([hidden])")
            page.screenshot(path=out / "landing-hero.png")
            page.locator("#hosted").screenshot(path=out / "landing-hosted.png")
        browser.close()
    print(f"screenshots in {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "allo-e2e-shots")
