#!/usr/bin/python3.13

import sys

# add files directory to path so pytkterm is accessible
# sys.path.append("/usr/share/PyTkTerm/")

import tkinter as tk
from tkinter import font as tkfont
from pytkterm import Terminal
import shutil

# closeable terminal tabs notebook
TERMINAL_TAB_ROW_BACKGROUND = "#1e1e1e"
TERMINAL_TAB_BACKGROUND = "#2d2d2d"
TERMINAL_ACTIVE_TAB_BACKGROUND = "#000000"
TERMINAL_TAB_FOREGROUND = "#d4d4d4"
TERMINAL_TAB_INDICATOR_COLOR = "#4a9eff"
TERMINAL_TAB_CLOSE_COLOR = "#808080"
TERMINAL_TAB_CLOSE_HOVER_COLOR = "#ff5555"
TERMINAL_TAB_CLOSE_IMAGE_SIZE = 9

def make_terminal_tab_close_image(master, color):
    # draw a two pixel wide X so no image files are needed
    size = TERMINAL_TAB_CLOSE_IMAGE_SIZE
    image = tk.PhotoImage(master=master, width=size, height=size)
    for i in range(size):
        for x in (i, min(i + 1, size - 1)):
            image.put(color, to=(x, i))
            image.put(color, to=(size - 1 - x, i))
    return image

class TerminalNotebookTab(tk.Frame):
    def __init__(self, notebook, content, title):
        super().__init__(notebook.tab_row, borderwidth=0)
        self.notebook = notebook
        self.content = content

        self.label = tk.Label(
            self, text=title, foreground=TERMINAL_TAB_FOREGROUND, borderwidth=0
        )
        self.label.grid(
            row=0,
            column=0,
            padx=(notebook.padding, 0),
            pady=notebook.padding // 2,
            sticky="nsw",
        )

        self.button = tk.Label(self, image=notebook.close_image, borderwidth=0)
        self.button.grid(row=0, column=1, padx=notebook.padding)

        self.indicator = tk.Frame(self, height=2, borderwidth=0)
        self.indicator.grid(row=1, column=0, columnspan=2, sticky="sew")

        self.bind("<1>", self.on_click)
        self.label.bind("<1>", self.on_click)
        self.button.bind("<1>", self.on_button_click)
        self.button.bind("<Enter>", self.on_button_enter)
        self.button.bind("<Leave>", self.on_button_leave)

        self.set_active(False)

    def set_active(self, active):
        if active:
            background = TERMINAL_ACTIVE_TAB_BACKGROUND
            indicator_background = TERMINAL_TAB_INDICATOR_COLOR
        else:
            background = TERMINAL_TAB_BACKGROUND
            indicator_background = TERMINAL_TAB_BACKGROUND

        self.configure(background=background)
        self.label.configure(background=background)
        self.button.configure(background=background)
        self.indicator.configure(background=indicator_background)

    def on_click(self, event):
        self.notebook.select(self.content)

    def on_button_click(self, event):
        # the notebook removes the tab when it sees the content being destroyed
        self.content.destroy()

    def on_button_enter(self, event):
        self.button.configure(image=self.notebook.close_hover_image)

    def on_button_leave(self, event):
        self.button.configure(image=self.notebook.close_image)

class TerminalNotebook(tk.Frame):
    def __init__(self, master):
        super().__init__(master, background=TERMINAL_TAB_ROW_BACKGROUND)
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)

        self.padding = tkfont.nametofont("TkDefaultFont").measure("M") // 2
        self.close_image = make_terminal_tab_close_image(self, TERMINAL_TAB_CLOSE_COLOR)
        self.close_hover_image = make_terminal_tab_close_image(
            self, TERMINAL_TAB_CLOSE_HOVER_COLOR
        )

        self.tab_row = tk.Frame(self, background=TERMINAL_TAB_ROW_BACKGROUND)
        self.tab_row.grid(row=0, column=0, sticky="new")

        self.tab_list = []
        self.current_tab = None

    def add(self, child, text):
        tab = TerminalNotebookTab(self, child, text)
        self.tab_list.append(tab)
        tab.grid(row=0, column=len(self.tab_list) - 1, sticky="nsw", padx=(0, 1))
        child.bind("<Destroy>", lambda event: self.on_content_destroy(event, tab), "+")
        self.select(child)

    def find_tab(self, child):
        for tab in self.tab_list:
            if tab.content is child:
                return tab

        raise ValueError

    def select(self, child):
        new_tab = self.find_tab(child)
        if new_tab is self.current_tab:
            return

        if self.current_tab:
            self.current_tab.content.grid_remove()
            self.current_tab.set_active(False)

        self.current_tab = new_tab
        new_tab.set_active(True)
        new_tab.content.grid(row=1, column=0, sticky="nsew")
        new_tab.content.focus_set()

    def tabs(self):
        return [tab.content for tab in self.tab_list]

    def on_content_destroy(self, event, tab):
        # destroy events of the content's own children also reach this binding
        if event.widget is not tab.content:
            return

        index = self.tab_list.index(tab)
        self.tab_list.remove(tab)
        tab.destroy()
        if tab is self.current_tab:
            self.current_tab = None

        for column, remaining_tab in enumerate(self.tab_list):
            remaining_tab.grid(column=column)

        # deferred so nothing is touched while the whole window is being destroyed
        self.after_idle(self.finish_tab_removal, index)

    def finish_tab_removal(self, index):
        if not self.winfo_exists():
            return

        # prefer the tab that took the closed tab's place
        if self.current_tab is None and self.tab_list:
            self.select(self.tab_list[min(index, len(self.tab_list) - 1)].content)

        self.event_generate("<<NotebookTabClosed>>")

# setup window
root = tk.Tk()
root.title("Terminal")

# set window icon
# icon = tk.PhotoImage(file="/usr/share/PyTkTerm/icon.png")
# root.iconphoto(True, icon)

# make closable terminal tabs
tabs = TerminalNotebook(root)
tabs.pack(fill="both", expand=True)

# close window when the last tab is closed
def close_window_if_no_tabs(*args):
    if not tabs.tabs():
        root.destroy()

tabs.bind("<<NotebookTabClosed>>", close_window_if_no_tabs)

# open new terminal function
def newterm(*args, **kwargs):

    # make and setup terminal widget
    tw = Terminal(tabs, *args, **kwargs)
    tw.bind("<Control-t>",
            lambda _: newterm(
            bg="black",
            fg="white",
            bd=0
            )
    )
    tabs.add(tw, text="Terminal")
    tw.focus_set()

    # bind process end to close tab
    tw.bind("<<TerminalStopped>>", lambda _, tw=tw: tw.destroy())

# if args are given and valid, set command for first terminal to args. otherwise, set default command (/bin/bash on linux and powershell.exe on windows)
args = sys.argv[1:]
if args and shutil.which(args[0]):
    command = args
else:
    command = None

# make first terminal
newterm(
    command=command,
    bg="black",
    fg="white",
    bd=0
)

# mainloop
root.mainloop()
