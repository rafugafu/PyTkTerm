# About  
This is a full, real, standalone terminal emulator inside a tkinter `Text` widget. It is the same as [PyNotes'](https://github.com/rafugafu/pynotes) integrated terminal, with a few modifications to make it completely standalone (removed `utils.show` messages and some references to `state`), affecting no behavior.  
# Specifications  
## Constructor  
`Terminal(master, command=None, endmessage=None, nocolor=False, *args, **kwargs)`  
- `master`: the parent tkinter widget.  
- `command`: a list of argv to run in place of the shell, for example `['ssh', 'host']`. Pass `None` to launch the default shell (`$SHELL` on Linux, `powershell.exe` on Windows).  
- `endmessage`: text appended to the widget and shown once the child process exits. Once it is shown, the widget stops forwarding keypresses to the child (there is none left) and instead treats any keypress as a request to tear down the pty/process and fire `<<TerminalStopped>>`; the widget itself is not destroyed, only cleaned up, and stays visible showing its final contents unless something bound to `<<TerminalStopped>>` destroys it. Pass `None` to skip the message and run that same teardown (firing `<<TerminalStopped>>`) automatically as soon as the child process exits, with no keypress needed.  
- `nocolor`: when `True`, disables ANSI SGR color/style rendering and cursor color escapes, keeping only plain text with cursor movement and editing.  
- `*args, **kwargs`: forwarded to `tkinter.Text`, so any `Text` option works (`background`, `foreground`, `font`, etc). `font` defaults to `(monospace, 12)` and `wrap` defaults to `'none'`.  
## Terminal Capabilities  
- Spawns the child process on a real pty on Linux (via `pty.openpty`) or `winpty` on Windows, and keeps the pty's window size in sync with the widget's size.  
- Supports a VT100/xterm-compatible escape sequence subset: cursor movement (CUU/CUD/CUF/CUB/CUP/HVP/CNL/CPL/CHA/VPA), save/restore cursor (`ESC 7`/`ESC 8`, CSI `s`/`u`), erase in line/display (EL, ED, modes 0-3), insert/delete character and line (`@`, `P`, `L`, `M`), scroll up/down (`S`, `T`), erase character (`X`), repeat character (`b`), scrolling regions (DECSTBM), tab stops (HTS/TBC), device status report and device attributes queries, and full reset (`RIS`).  
- Supports 16-color, 256-color, and 24-bit truecolor SGR rendering (bold, italic, underline, strikethrough, blink, reverse video, concealed text) unless `nocolor` is set.  
- Supports the alternate screen buffer (modes 47/1047/1049), origin mode (DECOM), reverse screen (DECSCNM), and DECCOLM-style screen clearing.  
- Supports bracketed paste mode, focus-in/focus-out reporting, application cursor keys mode, and `modifyOtherKeys` for modified special keys.  
- Supports mouse reporting in X10, normal, button-motion, and any-motion tracking modes, with both legacy and SGR (1006) coordinate encoding, plus scroll-wheel-to-arrow-key translation outside mouse mode via alternate scroll.  
- Supports OSC sequences for the clipboard (OSC 52), and for querying/setting the foreground color, background color, and cursor color (OSC 10/11/12/112).  
- Supports DECSCUSR cursor shape/blink selection (block, underline, bar; blinking or steady) and honors DECTCEM cursor visibility. The cursor is drawn as a custom overlay widget positioned over the current cell, not the `Text` widget's native insert cursor (which is disabled), so it can take on any of the three shapes, follow the child process's requested color, and blink independently while still showing the character underneath it in block shape.  
- Right-click opens a context menu with Copy, Paste, and Select All; selections can also be made and copied/pasted with the mouse or `Ctrl+Shift+C`/`Ctrl+Shift+V`.  
- Automatically adapts to `<<ThemeChanged>>` events and live tkinter color option changes.  
- Debounces widget resize events, then recomputes the character grid from the new pixel size, adjusts the visible scrollback window (or pads/trims each row to the new column count when in the alternate screen buffer, without rewrapping), and pushes the new size to the pty (`TIOCSWINSZ` plus `SIGWINCH`) so the child process is notified. Existing line content is never rewrapped to the new width.  
## Public Methods  
- `setcursortype(to = 'block')`: sets the cursor shape to `'block'`, `'underline'`, or `'bar'`.  
- `restart()`: terminates the running child process, clears the screen, and starts a new one with the same `command`.  
## Events  
- `<<TerminalProcessEnded>>`: fired once when the child process exits.  
- `<<TerminalStopped>>`: fired once the widget has fully torn down the process and its resources (skipped when restarting via `restart()`).  
- `<<TerminalOutputProcessed>>`: fired after each batch of child output has been processed and rendered.  
# Examples  
Basic:  
```python
import tkinter as tk
from PyTkTerm import Terminal

root = tk.Tk()
Terminal(root).pack(fill='both', expand=True)

root.mainloop()
```  
With focus and process end handling:  
```python
import tkinter as tk
from PyTkTerm import Terminal

# setup window
root = tk.Tk()

# make terminal widget and focus
tw = Terminal(root)
tw.pack(fill='both', expand=True)
tw.focus_set()

# bind process end to close window
tw.bind('<<TerminalStopped>>', lambda _: root.destroy())

# show window
root.mainloop()
```  
With window title and endmessage:
```python
import tkinter as tk
from PyTkTerm import Terminal

# setup window
root = tk.Tk()
root.title('Terminal')

# make terminal widget and focus
tw = Terminal(root, endmessage='--- process finished, press any key to continue ---')
tw.pack(fill='both', expand=True)
tw.focus_set()

# bind process end to close window
tw.bind('<<TerminalStopped>>', lambda _: root.destroy())

# show window
root.mainloop()
```  
# Dependencies  
- Python 3 with `tkinter` (standard library).  
- On Windows only: [`pywinpty`](https://pypi.org/project/pywinpty/) (imported as `winpty`), used in place of the standard library's `pty` module, which is Linux-only.  
- No other dependencies; everything else used (`os`, `platform`, `subprocess`, `codecs`, `sys`, `base64`, `re`, `threading`, `time`, and, on Linux, `pty`/`fcntl`/`termios`/`struct`/`select`/`signal`) is part of the Python standard library.  
# To Use  
1. Install the [dependencies](#dependencies).
2. Copy the PyTkTerm file into your program's directory. Then import `PyTkTerm.Terminal` normally. You can use this anywhere a normal tkinter `Text` widget can be used.  
