"""Visible MEMSYS desktop window. Not Chrome — a Tk app titled MEMSYS Desktop."""

from __future__ import annotations

import asyncio
import tkinter as tk
from tkinter import ttk

from cua.enums import LabMember, ScreenId

MEMBERS = {
    LabMember.ACTIVE: ("HERNANDEZ, MARIA", "Active", "1,204.55"),
    LabMember.DENIED: ("PATEL, AISHA", "denied", None),
    LabMember.LOCKED: ("WALKER, JAMES", "locked", None),
}


class TkMemsys:
    """In-process desktop MEMSYS. DesktopSurface talks to this by name / automationId."""

    title = "MEMSYS Desktop"

    def __init__(self, *, visible: bool = True) -> None:
        """Create the visible MEMSYS desktop window (sign-on first)."""
        self.visible = visible
        self.screen = ScreenId.SIGNON
        self.body = ""
        self.log: list[tuple] = []
        self.controls: dict[tuple[str, str], tk.Widget] = {}
        self.root = tk.Tk()
        self.root.title(self.title)
        self.root.geometry("520x360")
        self.banner = tk.StringVar(value="DESKTOP APP  —  not a browser")
        self.screen_var = tk.StringVar(value=ScreenId.SIGNON)
        self.status_var = tk.StringVar(value="")
        self._build()
        self._show_signon()
        if not visible:
            self.root.withdraw()
        self.root.update()

    def _build(self) -> None:
        """Build the Tk chrome (screen id, body, form area)."""
        pad = {"padx": 12, "pady": 6}
        tk.Label(
            self.root,
            textvariable=self.banner,
            font=("Segoe UI", 12, "bold"),
            fg="#0b3d0b",
            bg="#d4edda",
        ).pack(fill="x")
        tk.Label(self.root, textvariable=self.screen_var, font=("Consolas", 10)).pack(anchor="e", padx=12)
        self.form = ttk.Frame(self.root)
        self.form.pack(fill="both", expand=True, **pad)
        tk.Label(self.root, textvariable=self.status_var, wraplength=480, justify="left").pack(fill="x", padx=12, pady=8)

    def _clear_form(self) -> None:
        """Remove previous form widgets."""
        for child in self.form.winfo_children():
            child.destroy()
        self.controls.clear()

    def _entry(self, how: str, name: str, show: str = "") -> ttk.Entry:
        """Add a labeled entry and register it as a desktop locator."""
        widget = ttk.Entry(self.form, show=show, width=28)
        self.controls[(how, name)] = widget
        self.controls[("name", name)] = widget
        return widget

    def _button(self, label: str, command) -> ttk.Button:
        """Add a button and register it as a desktop locator."""
        widget = ttk.Button(self.form, text=label, command=command)
        self.controls[("name", label)] = widget
        self.controls[("text", label)] = widget
        return widget

    def _show_signon(self) -> None:
        """Show operator id / password / Sign On."""
        self._clear_form()
        self.screen = ScreenId.SIGNON
        self.screen_var.set(ScreenId.SIGNON)
        ttk.Label(self.form, text="Operator ID").grid(row=0, column=0, sticky="w", pady=4)
        opr = self._entry("automationId", "OPR")
        opr.grid(row=0, column=1, pady=4)
        ttk.Label(self.form, text="Password").grid(row=1, column=0, sticky="w", pady=4)
        pwd = self._entry("automationId", "PWD", show="*")
        pwd.grid(row=1, column=1, pady=4)
        self._button("Sign On", self._sign_on).grid(row=2, column=1, sticky="w", pady=8)
        self.status_var.set("Sign on to MEMSYS Desktop")
        self._refresh_body()

    def _show_inquiry(self) -> None:
        """Show member inquiry (MEMID + Search)."""
        self._clear_form()
        self.screen = ScreenId.INQUIRY
        self.screen_var.set(ScreenId.INQUIRY)
        ttk.Label(self.form, text="Member ID").grid(row=0, column=0, sticky="w", pady=4)
        mem = self._entry("automationId", "MEMID")
        mem.grid(row=0, column=1, pady=4)
        self._button("Search", self._search).grid(row=0, column=2, padx=8)
        self.status_var.set("Membership inquiry")
        self._refresh_body()

    def _sign_on(self) -> None:
        """Accept lab credentials and go to inquiry."""
        opr = self._value("OPR")
        pwd = self._value("PWD")
        if opr.upper() == "JLEE" and pwd == "demo":
            self._show_inquiry()
            return
        self.status_var.set("Invalid operator or password")
        self._refresh_body()

    def _search(self) -> None:
        """Look up the member id and show the result or an error."""
        member_id = self._value("MEMID").strip()
        if not member_id:
            self.status_var.set("Member ID is required")
            self.screen = ScreenId.INQUIRY
            self._refresh_body()
            return
        row = MEMBERS.get(member_id)
        if row is None:
            self.screen = ScreenId.INQUIRY
            self.screen_var.set(ScreenId.INQUIRY)
            self.status_var.set(f"Record not found for membership {member_id}")
            self._refresh_body()
            return
        name, status, balance = row
        if status == "denied":
            self.status_var.set(f"{name} does not have SERVICING rights")
            self._refresh_body()
            return
        if status == "locked":
            self.status_var.set(f"{name} is in locked status")
            self._refresh_body()
            return
        self.screen = ScreenId.MEMBER
        self.screen_var.set(ScreenId.MEMBER)
        self.status_var.set(
            f"Name: {name}\nStatus: {status}\nCurrent savings balance (S00 SAVINGS): {balance}"
        )
        self._refresh_body()

    def _value(self, name: str) -> str:
        """Current text in a named Tk variable."""
        widget = self.controls.get(("automationId", name)) or self.controls.get(("name", name))
        if widget is None or not hasattr(widget, "get"):
            return ""
        return widget.get()

    def _resolve(self, how: str, name: str) -> tk.Widget:
        """Resolve automationId/name locators to a Tk widget."""
        widget = self.controls.get((how, name))
        if widget is not None:
            return widget
        for key, item in self.controls.items():
            if key[1] == name:
                return item
        raise LookupError(f"no desktop control matched: {how}/{name}")

    def _refresh_body(self) -> None:
        """Pump Tk so the next snapshot sees the new screen."""
        self.body = "\n".join(
            [
                self.banner.get(),
                f"screen {self.screen_var.get()}",
                self.status_var.get(),
            ]
        )
        self.root.update()

    async def open(self, target: str) -> None:
        """Show this desktop app (sign-on or inquiry)."""
        self.log.append(("open", target))
        label = (target or "").strip()
        if label and label not in {"desktop:memsys", "{app}"}:
            self.banner.set(f"DESKTOP APP  —  {label}  —  not a browser")
            self.root.title(f"MEMSYS Desktop ({label})")
        self._show_signon()
        if self.visible:
            self.root.deiconify()
            self.root.lift()
            try:
                self.root.attributes("-topmost", True)
            except Exception:
                pass
        self.root.update()
        await asyncio.sleep(0.2)
        try:
            self.root.attributes("-topmost", False)
        except Exception:
            pass

    async def click(self, how: str, name: str) -> None:
        """Invoke a Tk button by locator."""
        widget = self._resolve(how, name)
        self.log.append(("click", how, name))
        widget.invoke()
        self.root.update()

    async def fill(self, how: str, name: str, value: str) -> None:
        """Set a Tk entry by locator."""
        widget = self._resolve(how, name)
        self.log.append(("fill", how, name, value))
        widget.delete(0, tk.END)
        widget.insert(0, value)
        self.root.update()

    async def press(self, key: str) -> None:
        """Handle Enter as Search or Sign On."""
        self.log.append(("press", key))
        if key.lower() in {"enter", "return"}:
            if self.screen == ScreenId.SIGNON:
                self._sign_on()
            else:
                self._search()
        self.root.update()

    async def snapshot(self) -> tuple[str, str | None]:
        """Return visible text and the screen code (SON01, INQ01, …)."""
        self._refresh_body()
        return self.body, self.screen

    def close(self) -> None:
        """Destroy the Tk window."""
        try:
            self.root.destroy()
        except Exception:
            pass
