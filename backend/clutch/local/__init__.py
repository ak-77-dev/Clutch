"""Desktop features that run on the player's own PC.

- ``library``: finds installed games across launchers and launches them
- ``playtime``: watches running processes and records play sessions
- ``capture``: rolling replay buffer (FFmpeg + WASAPI) that saves clips on a hotkey
- ``clips``: the media hub's clip store (thumbnails, trim, GIF export)

Stats features work anywhere; these only do something on Windows, and every
OS-specific call is isolated so the rest of the app and the tests run elsewhere.
"""
