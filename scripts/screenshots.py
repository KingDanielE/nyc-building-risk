"""Capture README screenshots of the running app (start it first: streamlit run app/app.py).

Uses the locally installed Microsoft Edge through Playwright, so no browser download.
Usage: .venv/Scripts/python scripts/screenshots.py  ->  docs/img/*.png
"""

from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "img"
BASE = "http://localhost:8501"


def wait_for_app(page: Page, extra_ms: int = 4000) -> None:
    page.wait_for_selector("[data-testid='stMain']", timeout=60_000)
    page.wait_for_function(  # "Running..." widget and grey placeholders while loading
        "!document.querySelector('[data-testid=\"stStatusWidget\"]')"
        " && !document.querySelector('[data-testid=\"stSkeleton\"]')", timeout=60_000)
    page.wait_for_timeout(extra_ms)  # map tiles / charts finish drawing


def hide_chrome(page: Page) -> None:
    page.add_style_tag(content="[data-testid='stHeader'], [data-testid='stToolbar'] "
                               "{ display: none !important; }")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", args=["--use-angle=swiftshader",
                                                            "--enable-unsafe-swiftshader"])
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)

        page.goto(BASE + "/")
        wait_for_app(page, 8000)
        hide_chrome(page)
        page.screenshot(path=OUT / "map.png")

        # Streamlit scrolls inside its own container, so full_page doesn't help: use a tall window
        page.set_viewport_size({"width": 1440, "height": 1750})
        page.goto(BASE + "/building")
        wait_for_app(page, 6000)
        hide_chrome(page)
        page.screenshot(path=OUT / "building.png")
        page.set_viewport_size({"width": 1440, "height": 900})

        page.goto(BASE + "/queue")
        wait_for_app(page, 8000)
        hide_chrome(page)
        page.screenshot(path=OUT / "queue.png")
        browser.close()
    for f in sorted(OUT.glob("*.png")):
        print(f.relative_to(ROOT), f"{f.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
