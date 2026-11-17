#ifndef UNICODE
#define UNICODE
#endif
#define _UNICODE
#include <windows.h>
#include <shlobj.h>
#include <shellapi.h>
#include <strsafe.h>

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR arguments, int show) {
    (void)instance; (void)previous; (void)show;
    SetCurrentProcessExplicitAppUserModelID(L"MSUTerrain.Desktop");
    HANDLE mutex = CreateMutexW(NULL, FALSE, L"Local\\MSUTerrain.Desktop.Instance");
    if (GetLastError() == ERROR_ALREADY_EXISTS) {
        MessageBoxW(NULL, L"MSU Terrain is already running. Switch to its window, or use Exit in the application before starting again.",
                    L"MSU Terrain", MB_OK | MB_ICONINFORMATION);
        if (mutex) CloseHandle(mutex);
        return 0;
    }
    WCHAR root[32768], python[32768], command[32768];
    if (!GetModuleFileNameW(NULL, root, ARRAYSIZE(root))) return 1;
    WCHAR *separator = wcsrchr(root, L'\\');
    if (!separator) return 1;
    *separator = 0;
    if (FAILED(StringCchPrintfW(python, ARRAYSIZE(python), L"%s\\runtime\\pythonw.exe", root)) ||
        FAILED(StringCchPrintfW(command, ARRAYSIZE(command),
            L"\"%s\" -I -B \"%s\\run_offline.py\" --desktop --installed %s", python, root, arguments))) return 1;
    STARTUPINFOW startup = {0};
    PROCESS_INFORMATION process = {0};
    startup.cb = sizeof(startup);
    if (!CreateProcessW(python, command, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, root, &startup, &process)) {
        MessageBoxW(NULL, L"The local application runtime could not start. Reinstall MSU Terrain using the offline installer.",
                    L"MSU Terrain", MB_OK | MB_ICONERROR);
        if (mutex) CloseHandle(mutex);
        return 1;
    }
    CloseHandle(process.hThread);
    WaitForSingleObject(process.hProcess, INFINITE);
    DWORD status = 1;
    GetExitCodeProcess(process.hProcess, &status);
    CloseHandle(process.hProcess);
    if (mutex) CloseHandle(mutex);
    if (status != 0) {
        MessageBoxW(NULL, L"MSU Terrain stopped unexpectedly. Diagnostic details are in %LOCALAPPDATA%\\MSU Terrain\\application.log.",
                    L"MSU Terrain", MB_OK | MB_ICONERROR);
    }
    return (int)status;
}
