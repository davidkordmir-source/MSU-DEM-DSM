Unicode true
Target amd64-unicode
!include "MUI2.nsh"
!include "x64.nsh"
Name "MSU Terrain"
OutFile "../downloads/MSU-Terrain-Setup-0.5.0-x64.exe"
InstallDir "$LOCALAPPDATA\Programs\MSU Terrain"
InstallDirRegKey HKCU "Software\MSU Terrain" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 32
Icon "../app/static/brand/app.ico"
UninstallIcon "../app/static/brand/app.ico"
VIProductVersion "0.5.0.0"
VIAddVersionKey "ProductName" "MSU Terrain"
VIAddVersionKey "FileDescription" "MSU Terrain Offline Desktop Setup"
VIAddVersionKey "FileVersion" "0.5.0"
VIAddVersionKey "ProductVersion" "0.5.0"
VIAddVersionKey "LegalCopyright" "MSU Terrain"

!define MUI_ABORTWARNING
!define MUI_ICON "../app/static/brand/app.ico"
!define MUI_UNICON "../app/static/brand/app.ico"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\MSU-Terrain.exe"
!define MUI_FINISHPAGE_RUN_NOTCHECKED
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Farsi"
!insertmacro MUI_LANGUAGE "English"

!macro CheckClosed
  System::Call 'kernel32::OpenMutexW(i 0x100000, i 0, w "Local\MSUTerrain.Desktop.Instance") p.r0'
  ${If} $0 != 0
    System::Call 'kernel32::CloseHandle(p r0)'
    MessageBox MB_OK|MB_ICONEXCLAMATION "Close MSU Terrain before installing, updating, or uninstalling." /SD IDOK
    Abort
  ${EndIf}
!macroend

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_OK|MB_ICONSTOP "This installer requires 64-bit Windows 10 or 11." /SD IDOK
    Abort
  ${EndIf}
  SetShellVarContext current
  SetRegView 64
  !insertmacro CheckClosed
FunctionEnd

Function un.onInit
  SetShellVarContext current
  SetRegView 64
  !insertmacro CheckClosed
FunctionEnd

Section "MSU Terrain" SEC_APP
  SetOutPath "$INSTDIR"
  File /r "../build/MSU-Terrain-Windows-x64/*.*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\MSU Terrain"
  CreateShortCut "$DESKTOP\MSU Terrain.lnk" "$INSTDIR\MSU-Terrain.exe" "" "$INSTDIR\MSU-Terrain.exe" 0
  CreateShortCut "$SMPROGRAMS\MSU Terrain\MSU Terrain.lnk" "$INSTDIR\MSU-Terrain.exe" "" "$INSTDIR\MSU-Terrain.exe" 0
  CreateShortCut "$SMPROGRAMS\MSU Terrain\Uninstall.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\MSU Terrain" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MSU Terrain" "DisplayName" "MSU Terrain"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MSU Terrain" "DisplayVersion" "0.5.0"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MSU Terrain" "Publisher" "MSU Terrain"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MSU Terrain" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MSU Terrain" "DisplayIcon" "$INSTDIR\MSU-Terrain.exe,0"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MSU Terrain" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MSU Terrain" "QuietUninstallString" '$\"$INSTDIR\Uninstall.exe$\" /S'
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MSU Terrain" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MSU Terrain" "NoRepair" 1
SectionEnd

Section "Uninstall"
  !include "../build/uninstall-files.nsh"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$DESKTOP\MSU Terrain.lnk"
  Delete "$SMPROGRAMS\MSU Terrain\MSU Terrain.lnk"
  Delete "$SMPROGRAMS\MSU Terrain\Uninstall.lnk"
  RMDir "$SMPROGRAMS\MSU Terrain"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MSU Terrain"
  DeleteRegKey HKCU "Software\MSU Terrain"
  ; LocalAppData results and any unknown user files are deliberately retained.
SectionEnd
