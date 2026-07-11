import reflex as rx

# Minimal Reflex host app for the F2-DR React-dedupe spike.
# Its only job: mount aethercal.ui.calendar.Calendar inside a REAL Reflex app and
# build the frontend (Vite / react-router build) so we can inspect how many copies
# of React end up in the output. See docs/spikes/f2-dr-react-dedupe.md.
config = rx.Config(
    app_name="f2demo",
    telemetry_enabled=False,
)
