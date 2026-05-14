; NSIS installer for DLPulse (Flet Windows ``runner\Release`` output).
; makensis /DSOURCE_DIR=C:/path/to/stage /DEXE_NAME=dlpulse.exe DLPulse.nsi
; OutFile is relative to this script → ../../build/DLPulse-Setup.exe

Unicode true

!ifndef SOURCE_DIR
  !error "Pass /DSOURCE_DIR=... (folder with Release contents)"
!endif
!ifndef EXE_NAME
  !define EXE_NAME "dlpulse.exe"
!endif

!define PRODUCT_NAME "DLPulse"

Name "${PRODUCT_NAME}"
OutFile "..\..\build\DLPulse-Setup.exe"
InstallDir "$PROGRAMFILES64\DLPulse"
RequestExecutionLevel admin

Page directory
Page instfiles

UninstPage uninstConfirm
UninstPage instfiles

Section "DLPulse"
  SetOutPath "$INSTDIR"
  File /r "${SOURCE_DIR}\*.*"
  CreateShortCut "$SMPROGRAMS\${PRODUCT_NAME}.lnk" "$INSTDIR\${EXE_NAME}" "" "" 0
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}" "DisplayName" "${PRODUCT_NAME}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}" "DisplayIcon" "$INSTDIR\${EXE_NAME}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}" "NoModify" 1
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}" "NoRepair" 1
  WriteUninstaller "$INSTDIR\uninstall.exe"
SectionEnd

Section Uninstall
  Delete "$SMPROGRAMS\${PRODUCT_NAME}.lnk"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}"
SectionEnd
