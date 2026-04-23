"""Built-in folder browser: navigate, new folder, rename, delete — no native OS picker."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import stat
import string
import sys
from pathlib import Path
from typing import Any, Callable

import flet as ft


def _expand_initial(initial: str | Path | None) -> Path:
    if not initial:
        return Path.home().absolute()
    p = Path(os.path.expanduser(str(initial)))
    try:
        if p.is_dir():
            with os.scandir(p):
                pass
            return p.absolute()
    except OSError:
        pass
    return Path.home().absolute()


def _safe_name(name: str) -> bool:
    name = name.strip()
    if not name or name in (".", ".."):
        return False
    if ".." in name or "/" in name or "\\" in name:
        return False
    if sys.platform == "win32" and re.search(r'[<>:"|?*]', name):
        return False
    return True


def _path_for_navigation(raw: str) -> tuple[Path | None, str | None]:
    """
    Validate the path for Go: must be an existing, listable directory.
    We avoid os.access() — on Linux it can be false even when listdir/scandir work.
    """
    raw = (raw or "").strip()
    if not raw:
        return None, "Enter a path."
    expanded = os.path.expanduser(raw)
    try:
        p = Path(expanded).absolute()
    except OSError as e:
        return None, str(e)
    try:
        st = p.stat()
    except OSError as e:
        return None, str(e)
    if not stat.S_ISDIR(st.st_mode):
        return None, "Not a directory."
    try:
        with os.scandir(p) as it:
            next(it, None)
    except OSError as e:
        return None, str(e)
    return p, None


def _list_entries(path: Path) -> tuple[list[Path], list[Path], str | None]:
    dirs: list[Path] = []
    files: list[Path] = []
    err: str | None = None
    try:
        if not path.is_dir():
            return [], [], "Not a directory"
        for ch in sorted(path.iterdir(), key=lambda x: x.name.lower()):
            try:
                if ch.is_dir():
                    if ch.name.startswith("."):
                        continue
                    dirs.append(ch)
                elif ch.is_file():
                    files.append(ch)
            except OSError:
                continue
    except OSError as e:
        err = str(e)
        if getattr(e, "errno", None) == 13:
            err = f"{e} (cannot list this folder — choose a location you can read, or chmod/chown.)"
    return dirs, files, err


def _windows_drives() -> list[Path]:
    out: list[Path] = []
    for d in string.ascii_uppercase:
        r = f"{d}:/"
        try:
            if os.path.exists(r):
                out.append(Path(r))
        except OSError:
            continue
    return out


async def show_folder_browser_dialog(
    page: ft.Page,
    *,
    initial: str | Path | None = None,
    title: str = "Choose folder",
    pick_mode: bool = True,
    dismiss_dialog_fn: Callable[[Any], None] | None = None,
) -> str | None:
    """
    pick_mode=True: return the chosen path or None (Cancel).
    pick_mode=False: browse only; return None when closed.
    """
    fut: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()

    def dismiss(ctrl: Any) -> None:
        if dismiss_dialog_fn:
            dismiss_dialog_fn(ctrl)
        else:
            ctrl.open = False
        page.update()

    current_dir = _expand_initial(initial)
    selected: Path | None = None
    pending_delete: Path | None = None

    path_field = ft.TextField(label="Path", dense=True, expand=True, text_size=13)
    err_text = ft.Text("", size=12, color=ft.Colors.RED_300)
    confirm_lbl = ft.Text("", size=12)
    tf_new = ft.TextField(label="New folder name", dense=True, width=240)
    tf_rename = ft.TextField(label="New name", dense=True, width=240)

    list_view = ft.ListView(spacing=2, padding=8, height=300, auto_scroll=True)

    async def apply_go(_: ft.ControlEvent | None = None) -> None:
        nonlocal current_dir, selected, pending_delete
        raw = (path_field.value or "").strip()
        cand, nav_err = _path_for_navigation(raw)
        if cand is None:
            err_text.value = nav_err or "Invalid path."
            page.update()
            return
        current_dir = cand
        selected = None
        pending_delete = None
        confirm_row.visible = False
        new_row.visible = False
        rename_row.visible = False
        await refresh()

    async def go_up(_: ft.ControlEvent) -> None:
        nonlocal current_dir, selected, pending_delete
        parent = current_dir.parent
        if sys.platform == "win32" and parent == current_dir:
            drives = _windows_drives()
            if drives:
                current_dir = drives[0]
        else:
            current_dir = parent
        selected = None
        pending_delete = None
        confirm_row.visible = False
        new_row.visible = False
        rename_row.visible = False
        await refresh()

    async def go_to(target: Path) -> None:
        nonlocal current_dir, selected, pending_delete
        try:
            t = target.absolute()
        except OSError:
            err_text.value = "Cannot open folder."
            page.update()
            return
        if not t.is_dir():
            return
        current_dir = t
        selected = None
        pending_delete = None
        confirm_row.visible = False
        new_row.visible = False
        rename_row.visible = False
        await refresh()

    def make_row(p: Path, *, is_dir: bool, sel: Path | None) -> ft.Container:
        def bg() -> str | None:
            if sel is not None and p == sel:
                return ft.Colors.with_opacity(0.14, ft.Colors.TEAL_300)
            return None

        async def on_main_click(_: ft.ControlEvent) -> None:
            nonlocal selected
            if is_dir:
                await go_to(p)
            else:
                selected = p
                await refresh()

        async def on_rename_item(_: ft.ControlEvent) -> None:
            nonlocal selected
            selected = p
            tf_rename.value = p.name
            rename_row.visible = True
            new_row.visible = False
            confirm_row.visible = False
            page.update()

        async def on_delete_item(_: ft.ControlEvent) -> None:
            nonlocal pending_delete
            pending_delete = p
            confirm_lbl.value = f"Delete “{p.name}”?"
            confirm_row.visible = True
            new_row.visible = False
            rename_row.visible = False
            page.update()

        menu = ft.PopupMenuButton(
            icon=ft.Icons.MORE_VERT,
            tooltip="Actions",
            items=[
                ft.PopupMenuItem(
                    content="Rename",
                    on_click=lambda e: asyncio.create_task(on_rename_item(e)),
                ),
                ft.PopupMenuItem(
                    content="Delete",
                    on_click=lambda e: asyncio.create_task(on_delete_item(e)),
                ),
            ],
        )

        name_label = ft.Text(
            p.name,
            expand=True,
            overflow=ft.TextOverflow.ELLIPSIS,
            max_lines=1,
            text_align=ft.TextAlign.START,
        )

        return ft.Container(
            content=ft.Row(
                [
                    ft.Icon(
                        ft.Icons.FOLDER if is_dir else ft.Icons.INSERT_DRIVE_FILE_OUTLINED,
                        size=22,
                        color=ft.Colors.TEAL_200 if is_dir else ft.Colors.BLUE_GREY_300,
                    ),
                    ft.TextButton(
                        content=ft.Row(
                            [name_label],
                            alignment=ft.MainAxisAlignment.START,
                            expand=True,
                        ),
                        on_click=lambda e: asyncio.create_task(on_main_click(e)),
                        style=ft.ButtonStyle(
                            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                        ),
                        expand=True,
                    ),
                    menu,
                ],
                alignment=ft.MainAxisAlignment.START,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=bg(),
            border_radius=6,
            padding=ft.Padding.symmetric(horizontal=4, vertical=0),
        )

    def make_drive_tile(d: Path) -> ft.Container:
        async def open_drive(_: ft.ControlEvent) -> None:
            await go_to(d)

        drive_label = ft.Text(
            str(d),
            expand=True,
            overflow=ft.TextOverflow.ELLIPSIS,
            max_lines=1,
            text_align=ft.TextAlign.START,
            weight=ft.FontWeight.W_500,
        )
        return ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.STORAGE, color=ft.Colors.AMBER_200),
                    ft.TextButton(
                        content=ft.Row(
                            [drive_label],
                            alignment=ft.MainAxisAlignment.START,
                            expand=True,
                        ),
                        on_click=lambda e: asyncio.create_task(open_drive(e)),
                        style=ft.ButtonStyle(padding=ft.Padding.symmetric(horizontal=8, vertical=4)),
                        expand=True,
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=ft.Colors.with_opacity(0.06, ft.Colors.WHITE),
            border_radius=6,
        )

    async def refresh() -> None:
        nonlocal selected
        err_text.value = ""
        path_field.value = str(current_dir)
        list_view.controls.clear()

        dirs, files, err = await asyncio.to_thread(_list_entries, current_dir)
        if err:
            err_text.value = err
            page.update()
            return

        if sys.platform == "win32" and current_dir == current_dir.parent:
            for d in _windows_drives():
                list_view.controls.append(make_drive_tile(d))

        parent = current_dir.parent
        if parent != current_dir:
            up_label = ft.Text(
                "..",
                expand=True,
                text_align=ft.TextAlign.START,
                weight=ft.FontWeight.W_500,
            )
            list_view.controls.append(
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Icon(ft.Icons.ARROW_UPWARD, color=ft.Colors.TEAL_300),
                            ft.TextButton(
                                content=ft.Row(
                                    [up_label],
                                    alignment=ft.MainAxisAlignment.START,
                                    expand=True,
                                ),
                                on_click=lambda e: asyncio.create_task(go_to(parent)),
                                style=ft.ButtonStyle(padding=ft.Padding.symmetric(horizontal=8, vertical=4)),
                                expand=True,
                            ),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    bgcolor=ft.Colors.with_opacity(0.06, ft.Colors.WHITE),
                    border_radius=6,
                )
            )

        for d in dirs:
            list_view.controls.append(make_row(d, is_dir=True, sel=selected))
        for f in files:
            list_view.controls.append(make_row(f, is_dir=False, sel=selected))
        page.update()

    async def do_refresh(_: ft.ControlEvent) -> None:
        await refresh()

    async def on_new_folder_click(_: ft.ControlEvent) -> None:
        new_row.visible = True
        rename_row.visible = False
        confirm_row.visible = False
        tf_new.value = ""
        page.update()

    async def on_mkdir(_: ft.ControlEvent) -> None:
        name = (tf_new.value or "").strip()
        if not _safe_name(name):
            err_text.value = "Invalid folder name."
            page.update()
            return
        dest = current_dir / name

        def _mkdir_new() -> None:
            # Do not call mkdir(False): the first arg is mode, and False becomes 0 → d--------- (no access).
            dest.mkdir(mode=0o755, parents=False, exist_ok=False)

        try:
            await asyncio.to_thread(_mkdir_new)
            if sys.platform != "win32":
                try:
                    os.chmod(dest, 0o755)
                except OSError:
                    pass
        except OSError as e:
            err_text.value = str(e)
            page.update()
            return
        new_row.visible = False
        err_text.value = ""
        await refresh()

    async def on_rename_apply(_: ft.ControlEvent) -> None:
        nonlocal selected
        if selected is None:
            return
        name = (tf_rename.value or "").strip()
        if not _safe_name(name):
            err_text.value = "Invalid name."
            page.update()
            return
        dest = selected.parent / name
        try:
            await asyncio.to_thread(selected.rename, dest)
        except OSError as e:
            err_text.value = str(e)
            page.update()
            return
        rename_row.visible = False
        selected = None
        err_text.value = ""
        await refresh()

    async def on_delete_confirm(_: ft.ControlEvent) -> None:
        nonlocal pending_delete, selected
        if pending_delete is None:
            confirm_row.visible = False
            page.update()
            return
        p = pending_delete
        try:
            if p.is_dir():
                await asyncio.to_thread(shutil.rmtree, p)
            else:
                await asyncio.to_thread(p.unlink)
        except OSError as e:
            err_text.value = str(e)
            page.update()
            return
        pending_delete = None
        selected = None
        confirm_row.visible = False
        err_text.value = ""
        await refresh()

    def on_delete_cancel(_: ft.ControlEvent) -> None:
        nonlocal pending_delete
        pending_delete = None
        confirm_row.visible = False
        page.update()

    confirm_row = ft.Row(
        [
            confirm_lbl,
            ft.TextButton("Cancel", on_click=on_delete_cancel),
            ft.FilledButton("Delete", color=ft.Colors.RED, on_click=lambda e: asyncio.create_task(on_delete_confirm(e))),
        ],
        visible=False,
    )

    new_row = ft.Row(
        [tf_new, ft.FilledButton("Create", icon=ft.Icons.CREATE_NEW_FOLDER, on_click=lambda e: asyncio.create_task(on_mkdir(e)))],
        visible=False,
    )

    rename_row = ft.Row(
        [tf_rename, ft.FilledButton("Apply", icon=ft.Icons.DRIVE_FILE_RENAME_OUTLINE, on_click=lambda e: asyncio.create_task(on_rename_apply(e)))],
        visible=False,
    )

    toolbar = ft.Row(
        [
            ft.IconButton(
                icon=ft.Icons.ARROW_UPWARD,
                tooltip="Up",
                on_click=lambda e: asyncio.create_task(go_up(e)),
            ),
            ft.IconButton(
                icon=ft.Icons.CREATE_NEW_FOLDER,
                tooltip="New folder",
                on_click=lambda e: asyncio.create_task(on_new_folder_click(e)),
            ),
            ft.IconButton(
                icon=ft.Icons.REFRESH,
                tooltip="Refresh",
                on_click=lambda e: asyncio.create_task(do_refresh(e)),
            ),
        ],
        spacing=4,
        wrap=True,
    )

    dlg_box: list[ft.AlertDialog | None] = [None]

    def finish(result: str | None) -> None:
        if not fut.done():
            fut.set_result(result)
        if dlg_box[0]:
            dismiss(dlg_box[0])

    def pick_current(_: ft.ControlEvent) -> None:
        if not fut.done():
            fut.set_result(str(current_dir))
        if dlg_box[0]:
            dismiss(dlg_box[0])
        page.update()

    actions: list[ft.Control] = []
    if pick_mode:
        actions.append(ft.TextButton("Cancel", on_click=lambda _: finish(None)))
        actions.append(ft.FilledButton("Use this folder", icon=ft.Icons.CHECK, on_click=pick_current))
    else:
        actions.append(ft.FilledButton("Close", on_click=lambda _: finish(None)))

    def _go_click(_: ft.ControlEvent) -> None:
        asyncio.create_task(apply_go())

    path_field.on_submit = _go_click

    content_col = ft.Column(
        [
            ft.Row(
                [
                    path_field,
                    ft.TextButton("Go", on_click=_go_click),
                ],
                alignment=ft.MainAxisAlignment.START,
            ),
            err_text,
            toolbar,
            confirm_row,
            new_row,
            rename_row,
            ft.Container(
                content=list_view,
                border=ft.Border.all(1, ft.Colors.GREY_700),
                border_radius=8,
                clip_behavior=ft.ClipBehavior.HARD_EDGE,
            ),
        ],
        spacing=8,
        tight=True,
        width=580,
    )

    def on_dismiss_dlg(_: ft.ControlEvent) -> None:
        if not fut.done():
            fut.set_result(None)

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text(title),
        content=ft.Container(content=content_col, padding=ft.padding.only(top=4)),
        actions=actions,
        actions_alignment=ft.MainAxisAlignment.END,
        on_dismiss=on_dismiss_dlg,
    )
    dlg_box[0] = dlg

    page.show_dialog(dlg)
    page.update()
    await refresh()

    return await fut
