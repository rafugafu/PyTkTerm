# About  
This is a full, real, standalone terminal inside a tkinter `Text` widget. It is the same as [PyNotes'](https://github.com/rafugafu/pynotes) integrated terminal, with a few modifications to make it completely standalone (removed `utils.show` messages and some references to `state`), affecting no behavior.  
# Specifications  
## Constructor  
`Terminal(master, command, endmessage, nocolor = False, *args, **kwargs)`  
- `master`: the parent tkinter widget.  
- `command`: a list of argv to run in place of the shell, for example `['ssh', 'host']`. Pass `None` to launch the default shell (`$SHELL` on Linux, `powershell.exe` on Windows).  
- `endmessage`: text appended to the widget and shown once the child process exits. Pass `None` to close/destroy the widget automatically instead of showing a message.  
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
- Supports DECSCUSR cursor shape/blink selection (block, underline, bar; blinking or steady) and honors DECTCEM cursor visibility.  
- Right-click opens a context menu with Copy, Paste, and Select All; selections can also be made and copied/pasted with the mouse or `Ctrl+Shift+C`/`Ctrl+Shift+V`.  
- Automatically adapts to `<<ThemeChanged>>` events and live tkinter color option changes, and resizes the pty and redraws on widget resize.  
## Public Methods  
- `setcursortype(to = 'block')`: sets the cursor shape to `'block'`, `'underline'`, or `'bar'`.  
- `restart()`: terminates the running child process, clears the screen, and starts a new one with the same `command`.  
## Events  
- `<<TerminalProcessEnded>>`: fired once when the child process exits.  
- `<<TerminalStopped>>`: fired once the widget has fully torn down the process and its resources (skipped when restarting via `restart()`).  
- `<<TerminalOutputProcessed>>`: fired after each batch of child output has been processed and rendered.  
# To Use  
Copy the PyTkTerm file into your program's directory. Then import `PyTkTerm.Terminal` normally. You can use this anywhere a normal tkinter `Text` widget can be used.  
