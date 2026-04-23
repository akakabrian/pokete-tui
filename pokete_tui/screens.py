"""Modal screens — Textual overlays for shell-level help etc.

Do NOT overlap Pokete's in-game menus. Pokete has its own '?' help, its
own inventory dialog, its own save/load screens — those belong to the
engine. Use app-scoped chord keys (ctrl+h) only.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Static


HELP_TEXT = """\
[bold]pokete-tui — Textual re-shell[/bold]

This is lxgr-linux's Pokete running behind a Textual UI. All the usual
Pokete keys still work — the shell adds windowing, a sidebar, and
(optionally) a remote agent API.

[bold cyan]Movement / menus[/bold cyan]
  arrow keys    walk / menu navigation
  enter         confirm / interact
  escape        back / pause menu
  a / b         Pokete in-game actions (depends on context)
  space         advance text / acknowledge

[bold cyan]Shell-only shortcuts[/bold cyan]
  ctrl+q        quit the Textual app
  ctrl+c        emergency quit
  ctrl+h        open this help
  escape        close this dialog (when open)

[bold cyan]CLI flags[/bold cyan]
  --save-dir DIR  override the Pokete save directory
  --agent PORT    start the REST API on localhost:PORT

Press [bold]escape[/bold] to return to the game.
"""


class HelpScreen(ModalScreen):
    """Shell-level help modal — NOT Pokete's in-game help."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close", priority=True),
        Binding("q", "dismiss", "Close", priority=True),
    ]

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
    }
    HelpScreen > Container {
        width: 72;
        height: 24;
        padding: 1 2;
        border: thick cyan;
        background: $surface;
    }
    HelpScreen Static {
        width: 100%;
        height: 100%;
    }
    """

    def compose(self) -> ComposeResult:
        with Container():
            yield Static(HELP_TEXT, id="help-body")
