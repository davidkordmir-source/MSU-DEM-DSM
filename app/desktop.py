"""Native Windows file selection for direct, copy-free raster processing."""

import os
from pathlib import Path


def pick_raster(title: str) -> Path | None:
    if os.name != "nt":
        raise RuntimeError("انتخاب مستقیم فایل فقط در نسخهٔ ویندوز در دسترس است.")
    import ctypes
    from ctypes import wintypes

    class OpenFileName(ctypes.Structure):
        _fields_ = [
            ("lStructSize", wintypes.DWORD),
            ("hwndOwner", wintypes.HWND),
            ("hInstance", wintypes.HINSTANCE),
            ("lpstrFilter", wintypes.LPCWSTR),
            ("lpstrCustomFilter", wintypes.LPWSTR),
            ("nMaxCustFilter", wintypes.DWORD),
            ("nFilterIndex", wintypes.DWORD),
            ("lpstrFile", wintypes.LPWSTR),
            ("nMaxFile", wintypes.DWORD),
            ("lpstrFileTitle", wintypes.LPWSTR),
            ("nMaxFileTitle", wintypes.DWORD),
            ("lpstrInitialDir", wintypes.LPCWSTR),
            ("lpstrTitle", wintypes.LPCWSTR),
            ("Flags", wintypes.DWORD),
            ("nFileOffset", wintypes.WORD),
            ("nFileExtension", wintypes.WORD),
            ("lpstrDefExt", wintypes.LPCWSTR),
            ("lCustData", ctypes.c_ssize_t),
            ("lpfnHook", ctypes.c_void_p),
            ("lpTemplateName", wintypes.LPCWSTR),
            ("pvReserved", ctypes.c_void_p),
            ("dwReserved", wintypes.DWORD),
            ("FlagsEx", wintypes.DWORD),
        ]

    buffer = ctypes.create_unicode_buffer(32768)
    dialog = OpenFileName()
    dialog.lStructSize = ctypes.sizeof(dialog)
    dialog.lpstrFile = ctypes.cast(buffer, wintypes.LPWSTR)
    dialog.nMaxFile = len(buffer)
    dialog.lpstrFilter = "GeoTIFF (*.tif;*.tiff)\0*.tif;*.tiff\0\0"
    dialog.nFilterIndex = 1
    dialog.lpstrTitle = title
    dialog.Flags = 0x00080000 | 0x00001000 | 0x00000800 | 0x00000008
    if ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(dialog)):
        return Path(buffer.value)
    error = ctypes.windll.comdlg32.CommDlgExtendedError()
    if error:
        raise RuntimeError(f"پنجرهٔ انتخاب فایل باز نشد (کد {error}).")
    return None


def pick_output_directory() -> Path | None:
    if os.name != "nt":
        raise RuntimeError("انتخاب پوشه فقط در نسخهٔ ویندوز در دسترس است.")
    import ctypes
    from ctypes import wintypes

    class BrowseInfo(ctypes.Structure):
        _fields_ = [
            ("hwndOwner", wintypes.HWND),
            ("pidlRoot", ctypes.c_void_p),
            ("pszDisplayName", wintypes.LPWSTR),
            ("lpszTitle", wintypes.LPCWSTR),
            ("ulFlags", wintypes.UINT),
            ("lpfn", ctypes.c_void_p),
            ("lParam", wintypes.LPARAM),
            ("iImage", ctypes.c_int),
        ]

    shell = ctypes.windll.shell32
    ole = ctypes.windll.ole32
    shell.SHBrowseForFolderW.argtypes = [ctypes.POINTER(BrowseInfo)]
    shell.SHBrowseForFolderW.restype = ctypes.c_void_p
    shell.SHGetPathFromIDListEx.argtypes = [ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    initialized = ole.CoInitializeEx(None, 2) in (0, 1)
    item = None
    try:
        name = ctypes.create_unicode_buffer(32768)
        info = BrowseInfo()
        info.pszDisplayName = ctypes.cast(name, wintypes.LPWSTR)
        info.lpszTitle = "پوشهٔ ذخیرهٔ نتایج را روی درایوی با فضای کافی انتخاب کنید"
        info.ulFlags = 0x0001 | 0x0010 | (0x0040 if initialized else 0)
        item = shell.SHBrowseForFolderW(ctypes.byref(info))
        if not item:
            return None
        if not shell.SHGetPathFromIDListEx(item, name, len(name), 0):
            raise RuntimeError("مسیر پوشهٔ انتخاب‌شده در دسترس نیست.")
        return Path(name.value)
    finally:
        if item:
            ole.CoTaskMemFree(item)
        if initialized:
            ole.CoUninitialize()
